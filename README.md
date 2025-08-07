# Transaction Processing Demo

A serverless transaction system with fraud detection - built for learning AWS Lambda, API Gateway, DynamoDB, and observability patterns.

## What it does

POST a transaction → fraud check → approve/decline → store in DB → send alerts

**Stack:** API Gateway + Python Lambdas + DynamoDB + SQS + CloudWatch/X-Ray

## Quick Start

```bash
# Deploy
npm install
npx cdk bootstrap  # first time only
npx cdk deploy

# Test (get API key from CDK output)
curl -X POST https://<API-URL>/transactions \
  -H "x-api-key: <KEY>" \
  -H "Content-Type: application/json" \
  -d '{"userId":"user-123","amount":100.50,"currency":"USD","merchantId":"shop-456"}'

# Generate test data
aws lambda invoke --function-name transaction-seed-data --payload '{}' /tmp/response.json
```

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
npm run build                    # Compile TypeScript
npm run test                     # Run Python tests
npm run cdk:deploy              # Deploy changes
npm run cdk:destroy             # Delete everything

# Project structure
lib/agentic-demo-app-stack.ts   # CDK infrastructure
lambda/*/index.py               # Python Lambda functions  
tests/                          # Python unit/integration tests
```

## Monitoring

- **Dashboard:** AWS Console → CloudWatch → Dashboards
- **Traces:** AWS Console → X-Ray → Traces  
- **Logs:** `aws logs tail /aws/lambda/transaction-service --follow`

## Common Issues

- **403 Forbidden:** Check API key in `x-api-key` header
- **High fraud scores:** Reduce test frequency (triggers velocity limits)  
- **No data:** Run seed function to populate fraud rules
