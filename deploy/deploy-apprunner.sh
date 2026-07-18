#!/usr/bin/env bash
# Deploys Document Analyzer to AWS App Runner via ECR.
#
# Prerequisites:
#   - AWS CLI v2 installed and configured (`aws configure`)
#   - Docker installed and running
#   - An ANTHROPIC_API_KEY exported in your shell (never hard-code it here)
#
# Usage:
#   export ANTHROPIC_API_KEY=sk-ant-...
#   AWS_REGION=us-east-1 APP_NAME=document-analyzer ./deploy/deploy-apprunner.sh

set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
APP_NAME="${APP_NAME:-document-analyzer}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${APP_NAME}"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: export ANTHROPIC_API_KEY before running this script." >&2
  exit 1
fi

echo "==> Creating ECR repository (if it doesn't exist)"
aws ecr describe-repositories --repository-names "${APP_NAME}" --region "${AWS_REGION}" \
  >/dev/null 2>&1 || aws ecr create-repository --repository-name "${APP_NAME}" --region "${AWS_REGION}"

echo "==> Logging in to ECR"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "==> Building image"
docker build -t "${APP_NAME}:latest" ..

echo "==> Tagging & pushing image"
docker tag "${APP_NAME}:latest" "${ECR_REPO}:latest"
docker push "${ECR_REPO}:latest"

echo "==> Storing ANTHROPIC_API_KEY in AWS Systems Manager Parameter Store (SecureString)"
aws ssm put-parameter \
  --name "/${APP_NAME}/ANTHROPIC_API_KEY" \
  --value "${ANTHROPIC_API_KEY}" \
  --type SecureString \
  --overwrite \
  --region "${AWS_REGION}"

echo "==> Creating App Runner access role for ECR (if it doesn't exist)"
ACCESS_ROLE_ARN=$(aws iam get-role --role-name AppRunnerECRAccessRole \
  --query 'Role.Arn' --output text 2>/dev/null || true)
if [[ -z "${ACCESS_ROLE_ARN}" ]]; then
  aws iam create-role --role-name AppRunnerECRAccessRole \
    --assume-role-policy-document file://apprunner-trust-policy.json
  aws iam attach-role-policy --role-name AppRunnerECRAccessRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
  ACCESS_ROLE_ARN=$(aws iam get-role --role-name AppRunnerECRAccessRole --query 'Role.Arn' --output text)
fi

echo "==> Creating/updating App Runner service"
aws apprunner create-service \
  --service-name "${APP_NAME}" \
  --region "${AWS_REGION}" \
  --source-configuration "{
    \"ImageRepository\": {
      \"ImageIdentifier\": \"${ECR_REPO}:latest\",
      \"ImageRepositoryType\": \"ECR\",
      \"ImageConfiguration\": {
        \"Port\": \"8080\",
        \"RuntimeEnvironmentSecrets\": {
          \"ANTHROPIC_API_KEY\": \"arn:aws:ssm:${AWS_REGION}:${ACCOUNT_ID}:parameter/${APP_NAME}/ANTHROPIC_API_KEY\"
        }
      }
    },
    \"AuthenticationConfiguration\": { \"AccessRoleArn\": \"${ACCESS_ROLE_ARN}\" },
    \"AutoDeploymentsEnabled\": true
  }" \
  --instance-configuration '{"Cpu":"1024","Memory":"2048"}' \
  || aws apprunner start-deployment --service-arn "$(aws apprunner list-services \
       --query "ServiceSummaryList[?ServiceName=='${APP_NAME}'].ServiceArn" --output text)"

echo "==> Done. Fetching service URL..."
aws apprunner list-services --region "${AWS_REGION}" \
  --query "ServiceSummaryList[?ServiceName=='${APP_NAME}'].ServiceUrl" --output text
