import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export class AgenticDemoAppStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

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
      readCapacity: 5,   // Low capacity to enable throttling for demo
      writeCapacity: 5,  // Low capacity to enable throttling for demo
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
      readCapacity: 5,   // Low capacity to enable throttling for demo
      writeCapacity: 5,  // Low capacity to enable throttling for demo
    });

    // Fraud Rules Table
    const fraudRulesTable = new dynamodb.Table(this, 'FraudRulesTable', {
      tableName: 'FraudRulesTable',
      partitionKey: {
        name: 'ruleId',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PROVISIONED,
      readCapacity: 5,   // Low capacity to enable throttling for demo
      writeCapacity: 5,  // Low capacity to enable throttling for demo
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

    // Create shared dependency layer
    const dependencyLayer = new lambda.LayerVersion(this, 'PythonDependencyLayer', {
      code: lambda.Code.fromAsset('layers/dependencies'),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_11],
      description: 'Common Python dependencies: aws-xray-sdk, requests'
    });

    // Lambda Function - Fraud Detection with X-Ray Tracing
    const fraudDetectionFunction = new lambda.Function(this, 'FraudDetectionFunction', {
      functionName: 'fraud-detection-service',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/fraud-detection'),
      layers: [dependencyLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 256, // 256MB as mentioned in plan for cost optimization
      environment: {
        FRAUD_RULES_TABLE: fraudRulesTable.tableName,
        TRANSACTIONS_TABLE: transactionsTable.tableName,
        RISK_THRESHOLD_HIGH: '80',
        RISK_THRESHOLD_MEDIUM: '50',
        AWS_XRAY_TRACING_NAME: 'fraud-detection-service',
      },
      logGroup: new logs.LogGroup(this, 'FraudDetectionLogGroup', {
        retention: logs.RetentionDays.ONE_YEAR,
      }),
      // Enable X-Ray tracing
      tracing: lambda.Tracing.ACTIVE,
    });

    // Grant Lambda permissions to read from DynamoDB tables
    fraudRulesTable.grantReadData(fraudDetectionFunction);
    transactionsTable.grantReadWriteData(fraudDetectionFunction);

    // Lambda Function - Transaction Service with X-Ray Tracing
    const transactionServiceFunction = new lambda.Function(this, 'TransactionServiceFunction', {
      functionName: 'transaction-service',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/transaction-service'),
      layers: [dependencyLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 256, // 256MB as mentioned in plan for cost optimization
      environment: {
        DYNAMODB_TABLE_TRANSACTIONS: transactionsTable.tableName,
        DYNAMODB_TABLE_FRAUD_RULES: fraudRulesTable.tableName,
        SQS_QUEUE_URL: notificationQueue.queueUrl,
        LAMBDA_FRAUD_FUNCTION_NAME: fraudDetectionFunction.functionName,
        AWS_XRAY_TRACING_NAME: 'transaction-service',
      },
      logGroup: new logs.LogGroup(this, 'TransactionServiceLogGroup', {
        retention: logs.RetentionDays.ONE_YEAR,
      }),
      // Enable X-Ray tracing
      tracing: lambda.Tracing.ACTIVE,
    });

    // Lambda Function - Scenario Control for Demo Management
    const scenarioControlFunction = new lambda.Function(this, 'ScenarioControlFunction', {
      functionName: 'scenario-control',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/scenario-control'),
      layers: [dependencyLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        SCENARIO_CONFIG_TABLE: scenarioConfigTable.tableName,
        AWS_XRAY_TRACING_NAME: 'scenario-control',
      },
      logGroup: new logs.LogGroup(this, 'ScenarioControlLogGroup', {
        retention: logs.RetentionDays.ONE_YEAR,
      }),
      tracing: lambda.Tracing.ACTIVE,
    });

    // Grant permissions to the transaction service Lambda
    transactionsTable.grantReadWriteData(transactionServiceFunction);
    fraudRulesTable.grantReadData(transactionServiceFunction);
    notificationQueue.grantSendMessages(transactionServiceFunction);
    fraudDetectionFunction.grantInvoke(transactionServiceFunction);

    // Grant permissions to the scenario control Lambda
    scenarioConfigTable.grantReadWriteData(scenarioControlFunction);
    
    // Grant CloudWatch Logs permissions to scenario control Lambda
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
    const apiKeySecret = new secretsmanager.Secret(this, 'ApiKeySecret', {
      secretName: 'transaction-api-key-secret',
      description: 'API Key for transaction processing',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ apiKey: apiKey.keyId }),
        generateStringKey: 'apiKey',
        excludeCharacters: ' "\\\''
      },
    });

    // Usage Plan
    const usagePlan = api.addUsagePlan('TransactionUsagePlan', {
      name: 'transaction-usage-plan',
      description: 'Usage plan for transaction processing API',
      throttle: {
        rateLimit: 10, // 10 requests per second as specified in plan
        burstLimit: 20,
      },
      quota: {
        limit: 10000, // 10,000 requests per month
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

    // Transaction Service Lambda Error Rate
    const transactionLambdaErrorAlarm = new cloudwatch.Alarm(this, 'TransactionLambdaErrorAlarm', {
      alarmName: 'transaction-service-lambda-errors',
      alarmDescription: 'Alarm when transaction service Lambda error rate is high',
      metric: transactionServiceFunction.metricErrors({
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5, // More than 5 errors in 5 minutes
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

    // Lambda Function Error Rate
    const lambdaErrorAlarm = new cloudwatch.Alarm(this, 'LambdaErrorAlarm', {
      alarmName: 'fraud-detection-lambda-errors',
      alarmDescription: 'Alarm when Lambda function error rate is high',
      metric: fraudDetectionFunction.metricErrors({
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5, // More than 5 errors in 5 minutes
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

    // Data Generator Lambda Function
    const dataGeneratorFunction = new lambda.Function(this, 'DataGeneratorFunction', {
      functionName: 'transaction-data-generator',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/data-generator'),
      layers: [dependencyLayer],
      timeout: cdk.Duration.minutes(5), // Allow time for multiple API calls
      memorySize: 256,
      environment: {
        API_GATEWAY_URL: api.url,
        API_KEY_SECRET_ARN: apiKeySecret.secretArn,
        SCENARIO_CONFIG_TABLE: scenarioConfigTable.tableName,
      },
      logGroup: new logs.LogGroup(this, 'DataGeneratorLogGroup', {
        retention: logs.RetentionDays.ONE_YEAR,
      }),
      tracing: lambda.Tracing.ACTIVE, // Enable X-Ray tracing
    });

    // Grant permissions to data generator
    apiKeySecret.grantRead(dataGeneratorFunction);
    fraudRulesTable.grantReadWriteData(dataGeneratorFunction);
    scenarioConfigTable.grantReadData(dataGeneratorFunction);

    // CloudWatch Events Rule to trigger data generator every 1 minute
    const dataGeneratorRule = new events.Rule(this, 'DataGeneratorRule', {
      ruleName: 'transaction-data-generator-schedule',
      description: 'Trigger transaction data generator every 1 minute',
      schedule: events.Schedule.rate(cdk.Duration.minutes(1)),
      enabled: true, // Set to false if you want to disable automatic data generation
    });

    // Add Lambda as target
    dataGeneratorRule.addTarget(new targets.LambdaFunction(dataGeneratorFunction));

    // Manual trigger Lambda for initial data seeding
    const seedDataFunction = new lambda.Function(this, 'SeedDataFunction', {
      functionName: 'transaction-seed-data',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.seed_handler',
      code: lambda.Code.fromAsset('lambda/data-generator'),
      layers: [dependencyLayer],
      timeout: cdk.Duration.minutes(10),
      memorySize: 512,
      environment: {
        API_GATEWAY_URL: api.url,
        API_KEY_SECRET_ARN: apiKeySecret.secretArn,
        FRAUD_RULES_TABLE: fraudRulesTable.tableName,
        SCENARIO_CONFIG_TABLE: scenarioConfigTable.tableName,
        SEED_MODE: 'true',
      },
      logGroup: new logs.LogGroup(this, 'SeedDataLogGroup', {
        retention: logs.RetentionDays.ONE_YEAR,
      }),
      tracing: lambda.Tracing.ACTIVE,
    });

    apiKeySecret.grantRead(seedDataFunction);
    fraudRulesTable.grantReadWriteData(seedDataFunction);
    scenarioConfigTable.grantReadData(seedDataFunction);

    // Notification Processor Lambda Function - Step 7: Customer Notification
    const notificationProcessorFunction = new lambda.Function(this, 'NotificationProcessorFunction', {
      functionName: 'notification-processor',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/notification-processor'),
      layers: [dependencyLayer],
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      logGroup: new logs.LogGroup(this, 'NotificationProcessorLogGroup', {
        retention: logs.RetentionDays.ONE_YEAR,
      }),
      tracing: lambda.Tracing.ACTIVE, // Enable X-Ray tracing
    });

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

    new cdk.CfnOutput(this, 'SeedDataFunctionName', {
      value: seedDataFunction.functionName,
      description: 'Seed Data Lambda Function Name (run once to populate initial data)'
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
  }
}
