import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as applicationsignals from 'aws-cdk-lib/aws-applicationsignals';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { AwsCustomResource, AwsCustomResourcePolicy, PhysicalResourceId } from 'aws-cdk-lib/custom-resources';
import { Tags } from 'aws-cdk-lib';
import { Construct } from 'constructs';

export class AgenticDemoAppStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Application Signals Discovery - Enable Application Signals service discovery
    const cfnDiscovery = new applicationsignals.CfnDiscovery(this,
      'AgenticDemoAppSignalsDiscovery', { }
    );

    // ADOT Lambda Layer for Application Signals instrumentation (us-east-1)
    const adotLayer = lambda.LayerVersion.fromLayerVersionArn(
      this, 'AwsLambdaLayerForOtel',
      'arn:aws:lambda:us-east-1:615299751070:layer:AWSOpenTelemetryDistroPython:16'
    );

    // DynamoDB Tables
    
    // Transactions Table
    const transactionsTable = new dynamodb.Table(this, 'TransactionsTable', {
      tableName: 'TransactionsTable',
      partitionKey: {
        name: 'transactionId',
        type: dynamodb.AttributeType.STRING
      },
      sortKey: {
        name: 'timestamp',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PROVISIONED,
      readCapacity: 1,   // Extremely low capacity to guarantee throttling exceptions
      writeCapacity: 1,  // Extremely low capacity to guarantee throttling exceptions
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY, // For demo purposes
    });

    // Global Secondary Index for user transaction history
    transactionsTable.addGlobalSecondaryIndex({
      indexName: 'UserTransactionHistoryIndex',
      partitionKey: {
        name: 'userId',
        type: dynamodb.AttributeType.STRING
      },
      sortKey: {
        name: 'timestamp',
        type: dynamodb.AttributeType.STRING
      },
      readCapacity: 1000,  // Higher capacity so fraud detection velocity checks don't throttle
      writeCapacity: 1000,  // Higher capacity for GSI writes (maintained by DynamoDB)
    });

    // Fraud Rules Table
    const fraudRulesTable = new dynamodb.Table(this, 'FraudRulesTable', {
      tableName: 'FraudRulesTable',
      partitionKey: {
        name: 'ruleId',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PROVISIONED,
      readCapacity: 1000,   // Higher capacity to handle fraud rule scans without throttling
      writeCapacity: 1000,   // Higher capacity since fraud rules are written once during seeding
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY, // For demo purposes
    });

    // Scenario Configuration Table for live demo control
    const scenarioConfigTable = new dynamodb.Table(this, 'ScenarioConfigTable', {
      tableName: 'ScenarioConfigTable',
      partitionKey: {
        name: 'configId',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, // On-demand for demo control
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY, // For demo purposes
    });

    // Recent Transactions Flow Table for real-time display in control plane
    const recentTransactionsFlowTable = new dynamodb.Table(this, 'RecentTransactionsFlowTable', {
      tableName: 'RecentTransactionsFlowTable',
      partitionKey: {
        name: 'pk',
        type: dynamodb.AttributeType.STRING  // "FLOW#YYYY-MM-DD"
      },
      sortKey: {
        name: 'sk',
        type: dynamodb.AttributeType.STRING  // "timestamp#correlationId"
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, // On-demand for demo usage
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      timeToLiveAttribute: 'ttl', // Auto-delete after 1 hour
      removalPolicy: cdk.RemovalPolicy.DESTROY, // For demo purposes
    });

    // SQS Queues
    
    // Dead Letter Queue
    const deadLetterQueue = new sqs.Queue(this, 'NotificationsDLQ', {
      queueName: 'notifications-dlq',
      retentionPeriod: cdk.Duration.days(14), // Maximum retention for DLQ
    });

    // Main Notification Queue
    const notificationQueue = new sqs.Queue(this, 'NotificationsQueue', {
      queueName: 'notifications-queue',
      retentionPeriod: cdk.Duration.days(4), // As specified in plan
      visibilityTimeout: cdk.Duration.seconds(300), // 5 minutes for processing
      deadLetterQueue: {
        queue: deadLetterQueue,
        maxReceiveCount: 3, // Retry 3 times before sending to DLQ
      },
    });

    // Custom dependency layer no longer needed - using ADOT layer for Application Signals

    // Note: Using IAM authentication instead of API secret for security

    // Lambda Function - Card Verification Service with ADOT Application Signals and Function URL
    const cardVerificationFunction = new lambda.Function(this, 'CardVerificationFunction', {
      functionName: 'card-verification-service',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/card-verification'),
      layers: [adotLayer],
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
      environment: {
        FAILURE_RATE: '0.01', // 1% failure rate by default
        AWS_LAMBDA_EXEC_WRAPPER: "/opt/otel-instrument",
        OTEL_RESOURCE_ATTRIBUTES: 'service.name=card-verification-service,deployment.environment=lambda,team.name=external-integrations,business.unit=financial-services,app=transaction-processor',
        OTEL_TRACES_SAMPLER: "always_on",
        OTEL_SERVICE_NAME: 'card-verification-service',
      },
      logGroup: new logs.LogGroup(this, 'CardVerificationLogGroup', {
        logGroupName: '/aws/lambda/agentic-demo-card-verification-service',
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Add Application Signals IAM policy to card verification function
    cardVerificationFunction.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchLambdaApplicationSignalsExecutionRolePolicy')
    );

    // Create Function URL for card verification service with IAM authentication
    const cardVerificationFunctionUrl = cardVerificationFunction.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM, // Use IAM authentication for security
      cors: {
        allowedOrigins: ['*'],
        allowedMethods: [lambda.HttpMethod.ALL],
        allowCredentials: true,
        allowedHeaders: ['*'],
        exposedHeaders: ['*']
      }
    });

    // Add service metadata tags for Card Verification Service
    Tags.of(cardVerificationFunction).add('app', 'Transaction Processor');
    Tags.of(cardVerificationFunction).add('team-name', 'External Integrations');
    Tags.of(cardVerificationFunction).add('business-unit', 'Financial Services');

    // Lambda Function - Fraud Detection with ADOT Application Signals
    const fraudDetectionFunction = new lambda.Function(this, 'FraudDetectionFunction', {
      functionName: 'fraud-detection-service',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/fraud-detection'),
      layers: [adotLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 256, // 256MB as mentioned in plan for cost optimization
      environment: {
        FRAUD_RULES_TABLE: fraudRulesTable.tableName,
        TRANSACTIONS_TABLE: transactionsTable.tableName,
        RISK_THRESHOLD_HIGH: '80',
        RISK_THRESHOLD_MEDIUM: '50',
        AWS_LAMBDA_EXEC_WRAPPER: "/opt/otel-instrument",
        OTEL_RESOURCE_ATTRIBUTES: 'service.name=fraud-detection-service,deployment.environment=lambda,team.name=risk-engineering,business.unit=risk-management,app=transaction-processor',
        OTEL_TRACES_SAMPLER: "always_on",
        OTEL_SERVICE_NAME: 'fraud-detection-service',
        // Fixed: Use root logger (like transaction service) + manual trace/span extraction
        OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED: "false", // Disable OTLP hijacking
        OTEL_PYTHON_LOG_CORRELATION: "true", // Enable trace/span ID correlation
        PYTHONUNBUFFERED: "1", // Force unbuffered output for immediate log visibility
        CACHE_BUST: Date.now().toString(), // Force redeploy with manual trace extraction
      },
      logGroup: new logs.LogGroup(this, 'FraudDetectionLogGroup', {
        logGroupName: '/aws/lambda/agentic-demo-fraud-detection',
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Add Application Signals IAM policy to fraud detection function
    fraudDetectionFunction.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchLambdaApplicationSignalsExecutionRolePolicy')
    );

    // Grant Lambda permissions to read/write DynamoDB tables (fraud detection needs write access for lazy initialization)
    fraudRulesTable.grantReadWriteData(fraudDetectionFunction);
    transactionsTable.grantReadWriteData(fraudDetectionFunction);

    // Add service metadata tags for Fraud Detection Service
    Tags.of(fraudDetectionFunction).add('app', 'Transaction Processor');
    Tags.of(fraudDetectionFunction).add('team-name', 'Risk Engineering');
    Tags.of(fraudDetectionFunction).add('business-unit', 'Risk Management');

    // Lambda Function - Rewards Eligibility Service with ADOT Application Signals
    const rewardsEligibilityFunction = new lambda.Function(this, 'RewardsEligibilityFunction', {
      functionName: 'rewards-eligibility-service',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/rewards-eligibility'),
      layers: [adotLayer],
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      environment: {
        USE_CACHE: 'false',
        REWARDS_QUERY_DELAY_MS: '1500',
        AWS_LAMBDA_EXEC_WRAPPER: "/opt/otel-instrument",
        OTEL_RESOURCE_ATTRIBUTES: 'service.name=rewards-eligibility-service,deployment.environment=lambda,team.name=loyalty-platform,business.unit=customer-operations,app=transaction-processor',
        OTEL_TRACES_SAMPLER: "always_on",
        OTEL_SERVICE_NAME: 'rewards-eligibility-service',
        // Fixed: Use root logger (like transaction service) + manual trace/span extraction
        OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED: "false", // Disable OTLP hijacking
        OTEL_PYTHON_LOG_CORRELATION: "true", // Enable trace/span ID correlation
        PYTHONUNBUFFERED: "1", // Force unbuffered output for immediate log visibility
        CACHE_BUST: Date.now().toString(), // Force redeploy with manual trace extraction
      },
      logGroup: new logs.LogGroup(this, 'RewardsEligibilityLogGroup', {
        logGroupName: '/aws/lambda/agentic-demo-rewards-eligibility-service',
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Add Application Signals IAM policy to rewards eligibility function
    rewardsEligibilityFunction.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchLambdaApplicationSignalsExecutionRolePolicy')
    );

    // Add service metadata tags for Rewards Eligibility Service
    Tags.of(rewardsEligibilityFunction).add('app', 'Transaction Processor');
    Tags.of(rewardsEligibilityFunction).add('team-name', 'Loyalty Platform');
    Tags.of(rewardsEligibilityFunction).add('business-unit', 'Customer Operations');

    // Lambda Function - Transaction Service with ADOT Application Signals
    const transactionServiceFunction = new lambda.Function(this, 'TransactionServiceFunction', {
      functionName: 'transaction-service',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/transaction-service'),
      layers: [adotLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 256, // 256MB as mentioned in plan for cost optimization
      environment: {
        DYNAMODB_TABLE_TRANSACTIONS: transactionsTable.tableName,
        DYNAMODB_TABLE_FRAUD_RULES: fraudRulesTable.tableName,
        RECENT_TRANSACTIONS_FLOW_TABLE: recentTransactionsFlowTable.tableName,
        SQS_QUEUE_URL: notificationQueue.queueUrl,
        LAMBDA_FRAUD_FUNCTION_NAME: fraudDetectionFunction.functionName,
        REWARDS_FUNCTION_NAME: rewardsEligibilityFunction.functionName,
        CARD_VERIFICATION_URL: cardVerificationFunctionUrl.url,
        MAX_RETRIES: '0', // Demo-configurable retry count for throttling scenarios
        BASE_DELAY_MS: '0.01', // Demo-configurable base delay for retry scenarios
        AWS_LAMBDA_EXEC_WRAPPER: "/opt/otel-instrument",
        OTEL_RESOURCE_ATTRIBUTES: 'service.name=transaction-service,deployment.environment=lambda,team.name=payments-core,business.unit=financial-services,app=transaction-processor',
        OTEL_TRACES_SAMPLER: "always_on",
        OTEL_SERVICE_NAME: 'transaction-service',
        // Fixed: Use root logger (like transaction service) + manual trace/span extraction
        OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED: "false", // Disable OTLP hijacking
        OTEL_PYTHON_LOG_CORRELATION: "true", // Enable trace/span ID correlation
        PYTHONUNBUFFERED: "1", // Force unbuffered output for immediate log visibility
        CACHE_BUST: Date.now().toString(), // Force redeploy with manual trace extraction
      },
      logGroup: new logs.LogGroup(this, 'TransactionServiceLogGroup', {
        logGroupName: '/aws/lambda/agentic-demo-transaction-service',
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Add Application Signals IAM policy to transaction service function
    transactionServiceFunction.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchLambdaApplicationSignalsExecutionRolePolicy')
    );

    // Store reference to transaction service log group for scenario control
    const transactionServiceLogGroup = transactionServiceFunction.logGroup;

    // Add service metadata tags for Transaction Service
    Tags.of(transactionServiceFunction).add('app', 'Transaction Processor');
    Tags.of(transactionServiceFunction).add('team-name', 'Payments Core');
    Tags.of(transactionServiceFunction).add('business-unit', 'Financial Services');

    // Lambda Function - Scenario Control for Demo Management with ADOT Application Signals
    const scenarioControlFunction = new lambda.Function(this, 'ScenarioControlFunction', {
      functionName: 'scenario-control',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/scenario-control'),
      layers: [adotLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        SCENARIO_CONFIG_TABLE: scenarioConfigTable.tableName,
        RECENT_TRANSACTIONS_FLOW_TABLE: recentTransactionsFlowTable.tableName,
        TRANSACTION_LOG_GROUP_NAME: transactionServiceLogGroup.logGroupName,
        AWS_LAMBDA_EXEC_WRAPPER: "/opt/otel-instrument",
        OTEL_TRACES_SAMPLER: "always_on",
      },
      logGroup: new logs.LogGroup(this, 'ScenarioControlLogGroup', {
        logGroupName: '/aws/lambda/agentic-demo-scenario-control',
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Add Application Signals IAM policy to scenario control function
    scenarioControlFunction.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchLambdaApplicationSignalsExecutionRolePolicy')
    );

    // Grant permissions to the transaction service Lambda
    transactionsTable.grantReadWriteData(transactionServiceFunction);
    fraudRulesTable.grantReadData(transactionServiceFunction);
    recentTransactionsFlowTable.grantReadWriteData(transactionServiceFunction);
    notificationQueue.grantSendMessages(transactionServiceFunction);
    fraudDetectionFunction.grantInvoke(transactionServiceFunction);
    rewardsEligibilityFunction.grantInvoke(transactionServiceFunction);
    
    // Grant transaction service permission to invoke the card verification Function URL
    cardVerificationFunction.grantInvokeUrl(transactionServiceFunction);

    // Grant permissions to the scenario control Lambda
    scenarioConfigTable.grantReadWriteData(scenarioControlFunction);
    recentTransactionsFlowTable.grantReadData(scenarioControlFunction);
    
    // Grant CloudWatch Logs permissions to scenario control Lambda (keeping for backward compatibility)
    scenarioControlFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'logs:StartQuery',
        'logs:GetQueryResults',
        'logs:DescribeLogGroups'
      ],
      resources: ['*'],
    }));

    // Grant CloudWatch permissions
    transactionServiceFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
    }));

    // Direct Lambda Integration - No VPC needed for serverless architecture

    // API Gateway with API Key and X-Ray Tracing
    const api = new apigateway.RestApi(this, 'TransactionApi', {
      restApiName: 'Transaction Processing API',
      description: 'API for processing transactions with fraud detection',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'X-Amz-Date', 'Authorization', 'X-Api-Key', 'X-Amzn-Trace-Id'],
      },
      // Enable X-Ray tracing
      deployOptions: {
        tracingEnabled: true,
      },
    });

    // API Key
    const apiKey = api.addApiKey('TransactionApiKey', {
      apiKeyName: 'transaction-api-key',
      description: 'API Key for Transaction Processing API'
    });

    // Store API Key in Secrets Manager for data generator
    // Note: This stores the key ID initially, but we use a custom resource to get the actual value
    const apiKeySecret = new secretsmanager.Secret(this, 'ApiKeySecret', {
      secretName: 'transaction-api-key-secret',
      description: 'API Key for transaction processing (stores actual key value)',
      secretStringValue: cdk.SecretValue.unsafePlainText(
        JSON.stringify({ 
          apiKey: `PLACEHOLDER-${apiKey.keyId}`,
          note: "This will be updated automatically by custom resource during deployment"
        })
      ),
    });

    // Custom resource to retrieve actual API key value and update secret during deployment
    const getApiKeyValue = new AwsCustomResource(this, 'GetApiKeyValue', {
      onCreate: {
        service: 'APIGateway',
        action: 'getApiKey',
        parameters: {
          apiKey: apiKey.keyId,
          includeValue: true
        },
        physicalResourceId: PhysicalResourceId.of(`api-key-retriever-${apiKey.keyId}`)
      },
      onUpdate: {
        service: 'APIGateway', 
        action: 'getApiKey',
        parameters: {
          apiKey: apiKey.keyId,
          includeValue: true
        },
        physicalResourceId: PhysicalResourceId.of(`api-key-retriever-${apiKey.keyId}`)
      },
      policy: AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: [
            'apigateway:GET',
            'apigateway:GetApiKey'
          ],
          resources: ['*']
        })
      ]),
      logGroup: new logs.LogGroup(this, 'GetApiKeyValueLogGroup', {
        logGroupName: '/aws/lambda/agentic-demo-get-api-key-value',
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Custom resource to update the secret with the actual API key value
    const updateApiKeySecret = new AwsCustomResource(this, 'UpdateApiKeySecret', {
      onCreate: {
        service: 'SecretsManager',
        action: 'putSecretValue',
        parameters: {
          SecretId: apiKeySecret.secretArn,
          SecretString: cdk.Fn.sub(
            '{"apiKey":"${ApiKeyValue}"}',
            {
              ApiKeyValue: getApiKeyValue.getResponseField('value')
            }
          )
        },
        physicalResourceId: PhysicalResourceId.of(`secret-updater-${apiKey.keyId}`)
      },
      onUpdate: {
        service: 'SecretsManager',
        action: 'putSecretValue', 
        parameters: {
          SecretId: apiKeySecret.secretArn,
          SecretString: cdk.Fn.sub(
            '{"apiKey":"${ApiKeyValue}"}',
            {
              ApiKeyValue: getApiKeyValue.getResponseField('value')
            }
          )
        },
        physicalResourceId: PhysicalResourceId.of(`secret-updater-${apiKey.keyId}`)
      },
      policy: AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: [
            'secretsmanager:PutSecretValue'
          ],
          resources: [apiKeySecret.secretArn]
        })
      ]),
      logGroup: new logs.LogGroup(this, 'UpdateApiKeySecretLogGroup', {
        logGroupName: '/aws/lambda/agentic-demo-update-api-key-secret',
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Ensure dependencies are correct
    updateApiKeySecret.node.addDependency(getApiKeyValue);
    updateApiKeySecret.node.addDependency(apiKeySecret);

    // Usage Plan
    const usagePlan = api.addUsagePlan('TransactionUsagePlan', {
      name: 'transaction-usage-plan',
      description: 'Usage plan for transaction processing API',
      // Temporarily removed throttling to restore service after demo
      quota: {
        limit: 1000000, // Increased to 1,000,000 requests per month for heavy demo usage
        period: apigateway.Period.MONTH,
      },
      apiStages: [{
        api: api,
        stage: api.deploymentStage,
      }],
    });

    usagePlan.addApiKey(apiKey);

    // Direct Lambda Integration with API Gateway (Serverless Architecture)
    const transactionLambdaIntegration = new apigateway.LambdaIntegration(transactionServiceFunction, {
      requestTemplates: { "application/json": '{ "statusCode": "200" }' }
    });

    // Add transactions resource to API Gateway
    const transactionsResource = api.root.addResource('transactions');
    transactionsResource.addMethod('POST', transactionLambdaIntegration, {
      apiKeyRequired: true,
    });

    // Add health check resource  
    const healthResource = api.root.addResource('health');
    healthResource.addMethod('GET', transactionLambdaIntegration, {
      apiKeyRequired: true,
    });

    // Add demo control endpoints for live scenario management
    const scenarioLambdaIntegration = new apigateway.LambdaIntegration(scenarioControlFunction);
    
    const demoResource = api.root.addResource('demo');
    
    // /demo/scenario - GET current scenario, POST to set scenario
    const scenarioResource = demoResource.addResource('scenario');
    scenarioResource.addMethod('GET', scenarioLambdaIntegration, {
      apiKeyRequired: true,
    });
    scenarioResource.addMethod('POST', scenarioLambdaIntegration, {
      apiKeyRequired: true,
    });
    
    // /demo/status - GET scenario status with timing info
    const statusResource = demoResource.addResource('status');
    statusResource.addMethod('GET', scenarioLambdaIntegration, {
      apiKeyRequired: true,
    });
    
    // /demo/reset - POST to reset to normal scenario
    const resetResource = demoResource.addResource('reset');
    resetResource.addMethod('POST', scenarioLambdaIntegration, {
      apiKeyRequired: true,
    });
    
    // /demo/scenarios - GET list of available scenarios
    const scenariosResource = demoResource.addResource('scenarios');
    scenariosResource.addMethod('GET', scenarioLambdaIntegration, {
      apiKeyRequired: true,
    });
    
    // /demo/flow - GET transaction flow from CloudWatch Logs
    const flowResource = demoResource.addResource('flow');
    flowResource.addMethod('GET', scenarioLambdaIntegration, {
      apiKeyRequired: true,
    });

    // CloudWatch Dashboard
    const dashboard = new cloudwatch.Dashboard(this, 'TransactionProcessingDashboard', {
      dashboardName: 'transaction-processing-dashboard',
    });

    // Transaction metrics widgets
    const transactionCountWidget = new cloudwatch.GraphWidget({
      title: 'Transaction Count by Status',
      left: [
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing',
          metricName: 'TransactionCount',
          dimensionsMap: { Status: 'APPROVED' },
          statistic: 'Sum',
        }),
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing', 
          metricName: 'TransactionCount',
          dimensionsMap: { Status: 'DECLINED' },
          statistic: 'Sum',
        }),
      ],
      width: 12,
      height: 6,
    });

    const processingTimeWidget = new cloudwatch.GraphWidget({
      title: 'Processing Time (P95)',
      left: [
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing',
          metricName: 'ProcessingTime',
          statistic: 'Average',
        }),
      ],
      width: 12,
      height: 6,
    });

    const errorRateWidget = new cloudwatch.GraphWidget({
      title: 'Error Rates',
      left: [
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing',
          metricName: 'ValidationError',
          statistic: 'Sum',
        }),
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing',
          metricName: 'ProcessingError', 
          statistic: 'Sum',
        }),
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing',
          metricName: 'CardVerificationError', 
          statistic: 'Sum',
        }),
      ],
      width: 12,
      height: 6,
    });

    // Lambda metrics
    const lambdaWidget = new cloudwatch.GraphWidget({
      title: 'Lambda Function Health',
      left: [
        transactionServiceFunction.metricDuration(),
        transactionServiceFunction.metricErrors(),
        fraudDetectionFunction.metricDuration(),
        fraudDetectionFunction.metricErrors(),
      ],
      width: 12,
      height: 6,
    });

    const lambdaInvocationsWidget = new cloudwatch.GraphWidget({
      title: 'Lambda Invocations',
      left: [
        transactionServiceFunction.metricInvocations(),
        fraudDetectionFunction.metricInvocations(),
      ],
      width: 12,
      height: 6,
    });

    // API Gateway metrics
    const apiGatewayWidget = new cloudwatch.GraphWidget({
      title: 'API Gateway Metrics',
      left: [
        api.metricCount(),
        api.metricLatency(),
        api.metricServerError(),
        api.metricClientError(),
      ],
      width: 12,
      height: 6,
    });

    // Throttling metrics widgets for demo scenarios
    const throttlingMetricsWidget = new cloudwatch.GraphWidget({
      title: 'DynamoDB Throttling Metrics',
      left: [
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing/Throttling',
          metricName: 'DynamoDBThrottling',
          statistic: 'Sum',
        }),
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing/Throttling', 
          metricName: 'TransactionThrottled',
          statistic: 'Sum',
        }),
      ],
      right: [
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing/Throttling',
          metricName: 'DynamoDBRetrySuccess',
          statistic: 'Sum',
        }),
        new cloudwatch.Metric({
          namespace: 'TransactionProcessing/Throttling',
          metricName: 'DynamoDBRetryExhausted', 
          statistic: 'Sum',
        }),
      ],
      width: 12,
      height: 6,
    });

    const throttlingRateWidget = new cloudwatch.GraphWidget({
      title: 'Throttling Rate and Success Rate',
      left: [
        new cloudwatch.MathExpression({
          expression: 'throttled / (throttled + successful) * 100',
          usingMetrics: {
            throttled: new cloudwatch.Metric({
              namespace: 'TransactionProcessing/Throttling',
              metricName: 'DynamoDBThrottling',
              statistic: 'Sum',
            }),
            successful: new cloudwatch.Metric({
              namespace: 'TransactionProcessing',
              metricName: 'TransactionCount',
              statistic: 'Sum',
            }),
          },
          label: 'Throttling Rate (%)',
        }),
      ],
      right: [
        new cloudwatch.MathExpression({
          expression: 'retrySuccess / (retrySuccess + retryExhausted) * 100',
          usingMetrics: {
            retrySuccess: new cloudwatch.Metric({
              namespace: 'TransactionProcessing/Throttling',
              metricName: 'DynamoDBRetrySuccess',
              statistic: 'Sum',
            }),
            retryExhausted: new cloudwatch.Metric({
              namespace: 'TransactionProcessing/Throttling',
              metricName: 'DynamoDBRetryExhausted',
              statistic: 'Sum',
            }),
          },
          label: 'Retry Success Rate (%)',
        }),
      ],
      width: 12,
      height: 6,
    });

    // Add widgets to dashboard
    dashboard.addWidgets(
      transactionCountWidget,
      processingTimeWidget
    );
    dashboard.addWidgets(
      errorRateWidget,
      lambdaWidget
    );
    dashboard.addWidgets(
      lambdaInvocationsWidget,
      apiGatewayWidget
    );
    dashboard.addWidgets(
      throttlingMetricsWidget,
      throttlingRateWidget
    );

    // CloudWatch Alarms for Critical Metrics
    
    // High Error Rate Alarm (>1%)
    const highErrorRateAlarm = new cloudwatch.Alarm(this, 'HighErrorRateAlarm', {
      alarmName: 'transaction-processing-high-error-rate',
      alarmDescription: 'Alarm when error rate exceeds 1%',
      metric: new cloudwatch.MathExpression({
        expression: '(validationErrors + processingErrors) / transactionCount * 100',
        usingMetrics: {
          validationErrors: new cloudwatch.Metric({
            namespace: 'TransactionProcessing',
            metricName: 'ValidationError',
            statistic: 'Sum',
          }),
          processingErrors: new cloudwatch.Metric({
            namespace: 'TransactionProcessing',
            metricName: 'ProcessingError',
            statistic: 'Sum',
          }),
          transactionCount: new cloudwatch.Metric({
            namespace: 'TransactionProcessing',
            metricName: 'TransactionCount',
            statistic: 'Sum',
          }),
        },
      }),
      threshold: 1.0, // 1% error rate
      evaluationPeriods: 3,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // High Latency Alarm (P95 > 1s)
    const highLatencyAlarm = new cloudwatch.Alarm(this, 'HighLatencyAlarm', {
      alarmName: 'transaction-processing-high-latency',
      alarmDescription: 'Alarm when P95 latency exceeds 1 second',
      metric: api.metricLatency({
        statistic: 'p95',
      }),
      threshold: 1000, // 1 second in milliseconds
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Transaction Service Lambda Faults Rate
    const transactionServiceFaultsAlarm = new cloudwatch.Alarm(this, 'TransactionServiceFaultsAlarm', {
      alarmName: 'transaction-service-lambda-faults',
      alarmDescription: 'Alarm when transaction service Lambda fault rate is high',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Fault',
        dimensionsMap: {
          Service: 'transaction-service',
          Environment: 'lambda',
        },
        statistic: 'Sum',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5, // More than 5 faults in 5 minutes
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Transaction Service Lambda Latency
    const transactionServiceLatencyAlarm = new cloudwatch.Alarm(this, 'TransactionServiceLatencyAlarm', {
      alarmName: 'transaction-service-lambda-latency',
      alarmDescription: 'Alarm when transaction service Lambda latency exceeds threshold',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Latency',
        dimensionsMap: {
          Environment: 'lambda',
          Service: 'transaction-service',
        },
        statistic: 'p95',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5000, // 5 seconds in milliseconds
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // SQS Dead Letter Queue Messages
    const sqsDeadLetterAlarm = new cloudwatch.Alarm(this, 'SqsDlqAlarm', {
      alarmName: 'transaction-processing-dlq-messages',
      alarmDescription: 'Alarm when messages appear in dead letter queue',
      metric: deadLetterQueue.metricNumberOfMessagesReceived(),
      threshold: 0, // Any message in DLQ is concerning
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // High SQS Queue Depth
    const sqsQueueDepthAlarm = new cloudwatch.Alarm(this, 'SqsQueueDepthAlarm', {
      alarmName: 'transaction-processing-queue-depth',
      alarmDescription: 'Alarm when SQS queue depth exceeds threshold',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/SQS',
        metricName: 'ApproximateNumberOfVisibleMessages',
        dimensionsMap: {
          QueueName: notificationQueue.queueName,
        },
        statistic: 'Average',
      }),
      threshold: 100, // More than 100 messages queued
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Fraud Detection Function Faults Rate
    const fraudDetectionFaultsAlarm = new cloudwatch.Alarm(this, 'FraudDetectionFaultsAlarm', {
      alarmName: 'fraud-detection-lambda-faults',
      alarmDescription: 'Alarm when fraud detection Lambda fault rate is high',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Fault',
        dimensionsMap: {
          Service: 'fraud-detection-service',
          Environment: 'lambda',
        },
        statistic: 'Sum',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5, // More than 5 faults in 5 minutes
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Throttling-specific alarms for demo scenarios
    
    // DynamoDB Throttling Detection
    const dynamoThrottlingAlarm = new cloudwatch.Alarm(this, 'DynamoThrottlingAlarm', {
      alarmName: 'dynamodb-throttling-detected',
      alarmDescription: 'Alarm when DynamoDB throttling is detected',
      metric: new cloudwatch.Metric({
        namespace: 'TransactionProcessing/Throttling',
        metricName: 'DynamoDBThrottling',
        statistic: 'Sum',
        period: cdk.Duration.minutes(1),
      }),
      threshold: 1, // Any throttling event triggers the alarm
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // High Transaction Throttling Rate
    const highThrottlingRateAlarm = new cloudwatch.Alarm(this, 'HighThrottlingRateAlarm', {
      alarmName: 'high-transaction-throttling-rate',
      alarmDescription: 'Alarm when transaction throttling rate exceeds 5%',
      metric: new cloudwatch.MathExpression({
        expression: 'IF(throttled + successful > 0, throttled / (throttled + successful) * 100, 0)',
        usingMetrics: {
          throttled: new cloudwatch.Metric({
            namespace: 'TransactionProcessing/Throttling',
            metricName: 'TransactionThrottled',
            statistic: 'Sum',
            period: cdk.Duration.minutes(5),
          }),
          successful: new cloudwatch.Metric({
            namespace: 'TransactionProcessing',
            metricName: 'TransactionCount',
            dimensionsMap: { Status: 'APPROVED' },
            statistic: 'Sum',
            period: cdk.Duration.minutes(5),
          }),
        },
      }),
      threshold: 5, // 5% throttling rate
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Retry Exhaustion Alarm
    const retryExhaustionAlarm = new cloudwatch.Alarm(this, 'RetryExhaustionAlarm', {
      alarmName: 'dynamodb-retry-exhaustion',
      alarmDescription: 'Alarm when DynamoDB retries are exhausted',
      metric: new cloudwatch.Metric({
        namespace: 'TransactionProcessing/Throttling',
        metricName: 'DynamoDBRetryExhausted',
        statistic: 'Sum',
        period: cdk.Duration.minutes(2),
      }),
      threshold: 5, // More than 5 retry exhaustions in 2 minutes
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Additional Lambda Service Alarms for Core Services

    // Card Verification Service - Faults Alarm
    const cardVerificationFaultsAlarm = new cloudwatch.Alarm(this, 'CardVerificationFaultsAlarm', {
      alarmName: 'card-verification-service-faults',
      alarmDescription: 'Alarm when card verification service fault rate is high',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Fault',
        dimensionsMap: {
          Service: 'card-verification-service',
          Environment: 'lambda',
        },
        statistic: 'Sum',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5, // More than 5 faults in 5 minutes
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Card Verification Service - Latency Alarm
    const cardVerificationLatencyAlarm = new cloudwatch.Alarm(this, 'CardVerificationLatencyAlarm', {
      alarmName: 'card-verification-service-latency',
      alarmDescription: 'Alarm when card verification service latency exceeds threshold',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Latency',
        dimensionsMap: {
          Environment: 'lambda',
          Service: 'card-verification-service',
        },
        statistic: 'p95',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5000, // 5 seconds in milliseconds
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Fraud Detection Service - Latency Alarm (faults alarm already exists)
    const fraudDetectionLatencyAlarm = new cloudwatch.Alarm(this, 'FraudDetectionLatencyAlarm', {
      alarmName: 'fraud-detection-service-latency',
      alarmDescription: 'Alarm when fraud detection service latency exceeds threshold',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Latency',
        dimensionsMap: {
          Environment: 'lambda',
          Service: 'fraud-detection-service',
        },
        statistic: 'p95',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5000, // 5 seconds in milliseconds
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });


    // Rewards Eligibility Service - Faults Alarm
    const rewardsEligibilityFaultsAlarm = new cloudwatch.Alarm(this, 'RewardsEligibilityFaultsAlarm', {
      alarmName: 'rewards-eligibility-service-faults',
      alarmDescription: 'Alarm when rewards eligibility service fault rate is high',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Fault',
        dimensionsMap: {
          Service: 'rewards-eligibility-service',
          Environment: 'lambda',
        },
        statistic: 'Sum',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5, // More than 5 faults in 5 minutes
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Rewards Eligibility Service - Latency Alarm
    const rewardsEligibilityLatencyAlarm = new cloudwatch.Alarm(this, 'RewardsEligibilityLatencyAlarm', {
      alarmName: 'rewards-eligibility-service-latency',
      alarmDescription: 'Alarm when rewards eligibility service latency exceeds threshold',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Latency',
        dimensionsMap: {
          Environment: 'lambda',
          Service: 'rewards-eligibility-service',
        },
        statistic: 'p95',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5000, // 5 seconds in milliseconds
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Application Signals SLOs - Commented out due to CDK deployment issues
    // Manual creation via console works but CDK has service discovery timing issues
    // 
    // Note: Latency SLO was successfully created but availability SLO consistently fails
    // Service discovery shows: Name="Transaction Processing API", Environment="api-gateway:prod"
    /*
    // API Gateway Latency SLO - P99 <= 30ms, 99.99% success over 3 rolling hours
    const apiGatewayLatencySlo = new applicationsignals.CfnServiceLevelObjective(this, 'ApiGatewayLatencySlo', {
      name: 'api-gateway-latency-slo',
      description: 'API Gateway P99 latency should be under 30ms for 99.99% of requests over 3 rolling hours',
      sli: {
        sliMetric: {
          keyAttributes: {
            'Name': 'Transaction Processing API',
            'Type': 'Service',
            'Environment': 'api-gateway:prod'
          },
          operationName: 'POST /transactions',
          metricType: 'LATENCY',
          periodSeconds: 60, // 1 minute periods
          statistic: 'p99'
        },
        metricThreshold: 30, // 30ms threshold
        comparisonOperator: 'LessThanOrEqualTo'
      },
      goal: {
        interval: {
          rollingInterval: {
            duration: 3,
            durationUnit: 'HOUR'
          }
        },
        attainmentGoal: 99.99, // 99.99% of requests should meet the latency target
        warningThreshold: 30.0 // Warn when 30% of error budget is consumed
      },
      tags: [
        {
          key: 'Service',
          value: 'TransactionProcessing'
        },
        {
          key: 'Component',
          value: 'APIGateway'
        }
      ]
    });

    // API Gateway Availability SLO - 99.99% availability over 3 rolling hours
    const apiGatewayAvailabilitySlo = new applicationsignals.CfnServiceLevelObjective(this, 'ApiGatewayAvailabilitySlo', {
      name: 'api-gateway-availability-slo',
      description: 'API Gateway should have 99.99% availability (non-5XX responses) over 3 rolling hours',
      sli: {
        sliMetric: {
          keyAttributes: {
            'Name': 'Transaction Processing API',
            'Type': 'Service', 
            'Environment': 'api-gateway:prod'
          },
          operationName: 'POST /transactions',
          metricType: 'AVAILABILITY',
          periodSeconds: 60 // 1 minute periods
        },
        metricThreshold: 99.99, // 99.99% availability threshold
        comparisonOperator: 'GreaterThanOrEqualTo'
      },
      goal: {
        interval: {
          rollingInterval: {
            duration: 3,
            durationUnit: 'HOUR'
          }
        },
        attainmentGoal: 99.99, // 99.99% availability
        warningThreshold: 30.0 // Warn when 30% of error budget is consumed
      },
      tags: [
        {
          key: 'Service',
          value: 'TransactionProcessing'
        },
        {
          key: 'Component', 
          value: 'APIGateway'
        }
      ]
    });
    */

    // Data Generator Lambda Function with ADOT Application Signals
    const dataGeneratorFunction = new lambda.Function(this, 'DataGeneratorFunction', {
      functionName: 'transaction-data-generator',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/data-generator'),
      layers: [adotLayer], // Keep layer for requests library, but no instrumentation
      timeout: cdk.Duration.minutes(10), // Extended for burst testing with 2000 transactions
      memorySize: 256,
      tracing: lambda.Tracing.DISABLED, // Disable X-Ray to prevent trace propagation in burst tests
      environment: {
        API_GATEWAY_URL: api.url,
        API_KEY_SECRET_ARN: apiKeySecret.secretArn,
        SCENARIO_CONFIG_TABLE: scenarioConfigTable.tableName,
        // Removed ADOT instrumentation environment variables to prevent trace creation
      },
      logGroup: new logs.LogGroup(this, 'DataGeneratorLogGroup', {
        logGroupName: '/aws/lambda/agentic-demo-data-generator',
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Skip Application Signals IAM policy for data generator (no ADOT instrumentation)
    // dataGeneratorFunction.role?.addManagedPolicy(
    //   iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchLambdaApplicationSignalsExecutionRolePolicy')
    // );

    // Grant permissions to data generator
    apiKeySecret.grantRead(dataGeneratorFunction);
    scenarioConfigTable.grantReadWriteData(dataGeneratorFunction);
    
    // Grant DynamoDB table update permissions for throttling demo WCU reset
    dataGeneratorFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:UpdateTable', 'dynamodb:DescribeTable'],
      resources: [transactionsTable.tableArn],
    }));
    
    // Grant Lambda configuration update permissions for rewards service cache toggle
    scenarioControlFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: ['lambda:UpdateFunctionConfiguration', 'lambda:GetFunction'],
      resources: [rewardsEligibilityFunction.functionArn]
    }));
    
    // Grant DynamoDB table management permissions for WCU reset
    scenarioControlFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:DescribeTable', 'dynamodb:UpdateTable'],
      resources: [transactionsTable.tableArn]
    }));

    // CloudWatch Events Rule to trigger data generator every 1 minute
    const dataGeneratorRule = new events.Rule(this, 'DataGeneratorRule', {
      ruleName: 'transaction-data-generator-schedule',
      description: 'Trigger transaction data generator every 1 minute',
      schedule: events.Schedule.rate(cdk.Duration.minutes(1)),
      enabled: true, // Set to false if you want to disable automatic data generation
    });

    // Add Lambda as target with normal scenario payload
    dataGeneratorRule.addTarget(new targets.LambdaFunction(dataGeneratorFunction, {
      event: events.RuleTargetInput.fromObject({
        forceScenario: 'normal'
      })
    }));

    // Periodic Throttling Demo Rule - triggers throttling demo every 1 hour
    const periodicThrottleRule = new events.Rule(this, 'PeriodicThrottleRule', {
      ruleName: 'periodic-throttling-demo-schedule',
      description: 'Trigger throttling demo every 1 hour for automated testing',
      schedule: events.Schedule.rate(cdk.Duration.hours(1)),
      enabled: false, // Disabled - trigger manually when needed for demos
    });

    // Add data generator as target with forceScenario override
    periodicThrottleRule.addTarget(new targets.LambdaFunction(dataGeneratorFunction, {
      event: events.RuleTargetInput.fromObject({
        forceScenario: 'demo_throttling'
      })
    }));


    // Notification Processor Lambda Function - Step 7: Customer Notification with ADOT Application Signals
    const notificationProcessorFunction = new lambda.Function(this, 'NotificationProcessorFunction', {
      functionName: 'notification-processor',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/notification-processor'),
      layers: [adotLayer],
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      environment: {
        AWS_LAMBDA_EXEC_WRAPPER: "/opt/otel-instrument",
        OTEL_RESOURCE_ATTRIBUTES: 'service.name=notification-processor,deployment.environment=lambda,team.name=customer-engagement,business.unit=customer-operations,app=transaction-processor',
        OTEL_TRACES_SAMPLER: "always_on",
        OTEL_SERVICE_NAME: 'notification-processor',
        // Fixed: Use root logger (like transaction service) + manual trace/span extraction
        OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED: "false", // Disable OTLP hijacking
        OTEL_PYTHON_LOG_CORRELATION: "true", // Enable trace/span ID correlation
        PYTHONUNBUFFERED: "1", // Force unbuffered output for immediate log visibility
        CACHE_BUST: Date.now().toString(), // Force redeploy with manual trace extraction
      },
      logGroup: new logs.LogGroup(this, 'NotificationProcessorLogGroup', {
        logGroupName: '/aws/lambda/agentic-demo-notification-processor',
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Add Application Signals IAM policy to notification processor function
    notificationProcessorFunction.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchLambdaApplicationSignalsExecutionRolePolicy')
    );

    // Grant permissions for notification processor to send CloudWatch metrics
    notificationProcessorFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
    }));

    // Configure SQS as event source for notification processor
    notificationProcessorFunction.addEventSource(new lambdaEventSources.SqsEventSource(notificationQueue, {
      batchSize: 10, // Process up to 10 messages at once
      maxBatchingWindow: cdk.Duration.seconds(5), // Wait up to 5 seconds to collect batch
      reportBatchItemFailures: true, // Enable partial batch failure reporting
    }));

    // Add service metadata tags for Notification Processor Service
    Tags.of(notificationProcessorFunction).add('app', 'Transaction Processor');
    Tags.of(notificationProcessorFunction).add('team-name', 'Customer Engagement');
    Tags.of(notificationProcessorFunction).add('business-unit', 'Customer Operations');

    // Notification Processor Service Alarms

    // Notification Processor Service - Faults Alarm
    const notificationProcessorFaultsAlarm = new cloudwatch.Alarm(this, 'NotificationProcessorFaultsAlarm', {
      alarmName: 'notification-processor-service-faults',
      alarmDescription: 'Alarm when notification processor service fault rate is high',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Fault',
        dimensionsMap: {
          Service: 'notification-processor',
          Environment: 'lambda',
        },
        statistic: 'Sum',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5, // More than 5 faults in 5 minutes
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Notification Processor Service - Latency Alarm
    const notificationProcessorLatencyAlarm = new cloudwatch.Alarm(this, 'NotificationProcessorLatencyAlarm', {
      alarmName: 'notification-processor-service-latency',
      alarmDescription: 'Alarm when notification processor service latency exceeds threshold',
      metric: new cloudwatch.Metric({
        namespace: 'ApplicationSignals',
        metricName: 'Latency',
        dimensionsMap: {
          Environment: 'lambda',
          Service: 'notification-processor',
        },
        statistic: 'p95',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5000, // 5 seconds in milliseconds
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // Output the table names for reference
    new cdk.CfnOutput(this, 'TransactionsTableName', {
      value: transactionsTable.tableName,
      description: 'DynamoDB Transactions Table Name'
    });

    new cdk.CfnOutput(this, 'FraudRulesTableName', {
      value: fraudRulesTable.tableName,
      description: 'DynamoDB Fraud Rules Table Name'
    });

    new cdk.CfnOutput(this, 'NotificationQueueUrl', {
      value: notificationQueue.queueUrl,
      description: 'SQS Notification Queue URL'
    });

    new cdk.CfnOutput(this, 'NotificationQueueArn', {
      value: notificationQueue.queueArn,
      description: 'SQS Notification Queue ARN'
    });

    new cdk.CfnOutput(this, 'FraudDetectionFunctionName', {
      value: fraudDetectionFunction.functionName,
      description: 'Lambda Fraud Detection Function Name'
    });

    new cdk.CfnOutput(this, 'FraudDetectionFunctionArn', {
      value: fraudDetectionFunction.functionArn,
      description: 'Lambda Fraud Detection Function ARN'
    });

    new cdk.CfnOutput(this, 'ApiGatewayUrl', {
      value: api.url,
      description: 'API Gateway URL'
    });

    new cdk.CfnOutput(this, 'ApiKeyId', {
      value: apiKey.keyId,
      description: 'API Key ID (use AWS CLI to get the actual key value)'
    });

    new cdk.CfnOutput(this, 'TransactionServiceFunctionName', {
      value: transactionServiceFunction.functionName,
      description: 'Transaction Service Lambda Function Name'
    });

    new cdk.CfnOutput(this, 'TransactionServiceFunctionArn', {
      value: transactionServiceFunction.functionArn,
      description: 'Transaction Service Lambda Function ARN'
    });

    new cdk.CfnOutput(this, 'DashboardUrl', {
      value: `https://${this.region}.console.aws.amazon.com/cloudwatch/home?region=${this.region}#dashboards:name=${dashboard.dashboardName}`,
      description: 'CloudWatch Dashboard URL'
    });

    new cdk.CfnOutput(this, 'DataGeneratorFunctionName', {
      value: dataGeneratorFunction.functionName,
      description: 'Data Generator Lambda Function Name'
    });


    new cdk.CfnOutput(this, 'NotificationProcessorFunctionName', {
      value: notificationProcessorFunction.functionName,
      description: 'Notification Processor Lambda Function Name (processes SQS notification queue)'
    });

    new cdk.CfnOutput(this, 'ApiKeySecretArn', {
      value: apiKeySecret.secretArn,
      description: 'API Key Secret ARN (use AWS CLI to get the actual key value)'
    });

    new cdk.CfnOutput(this, 'ScenarioConfigTableName', {
      value: scenarioConfigTable.tableName,
      description: 'DynamoDB Scenario Configuration Table Name for demo control'
    });

    new cdk.CfnOutput(this, 'ScenarioControlFunctionName', {
      value: scenarioControlFunction.functionName,
      description: 'Scenario Control Lambda Function Name for demo management'
    });

    new cdk.CfnOutput(this, 'RecentTransactionsFlowTableName', {
      value: recentTransactionsFlowTable.tableName,
      description: 'DynamoDB Recent Transactions Flow Table Name for real-time display'
    });

    new cdk.CfnOutput(this, 'RewardsEligibilityFunctionName', {
      value: rewardsEligibilityFunction.functionName,
      description: 'Rewards Eligibility Service Lambda Function Name (bad code push demo)'
    });

    // SLO outputs - Commented out since SLOs are commented out
    /*
    new cdk.CfnOutput(this, 'ApiGatewayLatencySloName', {
      value: apiGatewayLatencySlo.name!,
      description: 'API Gateway Latency SLO Name (P99 <= 30ms, 99.99% over 3 rolling hours)'
    });

    new cdk.CfnOutput(this, 'ApiGatewayAvailabilitySloName', {
      value: apiGatewayAvailabilitySlo.name!,
      description: 'API Gateway Availability SLO Name (99.99% availability over 3 rolling hours)'
    });
    */
  }
}
