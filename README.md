# Transaction Processing Demo

A serverless transaction system with fraud detection - built for learning AWS Lambda, API Gateway, DynamoDB, and observability patterns.

## What it does

POST a transaction → fraud check → approve/decline → store in DB → send alerts

**Stack:** API Gateway + Python Lambdas + DynamoDB + SQS + CloudWatch/X-Ray

## Quick Start (Fresh AWS Account)

```bash
# 1. Install dependencies
npm install

# 2. Build the CDK code
npm run build

# 3. Bootstrap CDK (one-time per account/region)
npm run cdk:bootstrap -- --profile default

# 4. Deploy the infrastructure
npm run cdk:deploy -- --profile default --require-approval never

# 5. Get API key value
aws apigateway get-api-key --api-key <KEY-ID> --include-value --profile default

# 6. Test the API
curl -X POST https://<API-URL>/transactions \
  -H "x-api-key: <ACTUAL-KEY-VALUE>" \
  -H "Content-Type: application/json" \
  -d '{"userId":"user-123","amount":100.50,"currency":"USD","merchantId":"shop-456"}'

# 7. Generate test data and seed fraud rules
aws lambda invoke --function-name transaction-seed-data --payload '{}' /tmp/response.json --profile default
```

> **⚠️ Important:** Always use `npm run cdk:*` commands instead of `npx cdk` or global `cdk` to ensure version compatibility. This project pins specific CDK CLI and library versions for consistent deployment across different environments.

## Architecture

```
API Gateway → Transaction Lambda → Fraud Lambda → DynamoDB
     ↓              ↓                              ↓
 Rate Limit    Validation              SQS Notifications
              Risk Score              CloudWatch Metrics
```

**Fraud Rules:**
- Amount >$10k = HIGH risk (declined)
- >10 transactions/hour = HIGH risk  
- Unknown locations = MEDIUM risk
- Everything else = LOW risk (approved)

## Development

```bash
# Build & test
npm run build                              # Compile TypeScript
npm run test                               # Run Python tests
npm run cdk:deploy -- --profile default   # Deploy changes  
npm run cdk:destroy -- --profile default  # Delete everything
npm run cdk:diff -- --profile default     # Show changes before deploy
npm run cdk:synth                          # Generate CloudFormation templates

# Project structure
lib/agentic-demo-app-stack.ts   # CDK infrastructure
lambda/*/index.py               # Python Lambda functions  
tests/                          # Python unit/integration tests
package.json                    # Pinned CDK versions for compatibility
```

## Monitoring

- **Dashboard:** AWS Console → CloudWatch → Dashboards
- **Traces:** AWS Console → X-Ray → Traces  
- **Logs:** `aws logs tail /aws/lambda/transaction-service --follow`

## Common Issues

- **403 Forbidden:** Check API key in `x-api-key` header
- **High fraud scores:** Reduce test frequency (triggers velocity limits)  
- **No data:** Run seed function to populate fraud rules
- **CDK version mismatch:** Use `npm run cdk:*` commands, not global `cdk`
- **Schema incompatibility:** Run `npm install` to ensure pinned versions are used

## Version Compatibility

This project uses pinned CDK versions for consistent deployment:
- **aws-cdk:** `2.1024.0` (CLI)
- **aws-cdk-lib:** `2.210.0` (library)

These versions are tested and compatible. If you encounter version issues, ensure you're using the npm scripts rather than global CDK commands.
