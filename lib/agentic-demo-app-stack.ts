import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
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
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, // Pay per request for demo
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecovery: true,
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
    });

    // Fraud Rules Table
    const fraudRulesTable = new dynamodb.Table(this, 'FraudRulesTable', {
      tableName: 'FraudRulesTable',
      partitionKey: {
        name: 'ruleId',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecovery: true,
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

    // Lambda Function - Fraud Detection with X-Ray Tracing
    const fraudDetectionFunction = new lambda.Function(this, 'FraudDetectionFunction', {
      functionName: 'fraud-detection-service',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/fraud-detection'),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256, // 256MB as mentioned in plan for cost optimization
      environment: {
        FRAUD_RULES_TABLE: fraudRulesTable.tableName,
        TRANSACTIONS_TABLE: transactionsTable.tableName,
        RISK_THRESHOLD_HIGH: '80',
        RISK_THRESHOLD_MEDIUM: '50',
        AWS_XRAY_TRACING_NAME: 'fraud-detection-service',
      },
      logRetention: logs.RetentionDays.ONE_WEEK,
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
      timeout: cdk.Duration.seconds(30),
      memorySize: 256, // 256MB as mentioned in plan for cost optimization
      environment: {
        DYNAMODB_TABLE_TRANSACTIONS: transactionsTable.tableName,
        DYNAMODB_TABLE_FRAUD_RULES: fraudRulesTable.tableName,
        SQS_QUEUE_URL: notificationQueue.queueUrl,
        LAMBDA_FRAUD_FUNCTION_NAME: fraudDetectionFunction.functionName,
        AWS_XRAY_TRACING_NAME: 'transaction-service',
      },
      logRetention: logs.RetentionDays.ONE_WEEK,
      // Enable X-Ray tracing
      tracing: lambda.Tracing.ACTIVE,
    });

    // Grant permissions to the transaction service Lambda
    transactionsTable.grantReadWriteData(transactionServiceFunction);
    fraudRulesTable.grantReadData(transactionServiceFunction);
    notificationQueue.grantSendMessages(transactionServiceFunction);
    fraudDetectionFunction.grantInvoke(transactionServiceFunction);

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

    // Data Generator Lambda Function
    const dataGeneratorFunction = new lambda.Function(this, 'DataGeneratorFunction', {
      functionName: 'transaction-data-generator',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/data-generator'),
      timeout: cdk.Duration.minutes(5), // Allow time for multiple API calls
      memorySize: 256,
      environment: {
        API_GATEWAY_URL: api.url,
        API_KEY_SECRET_ARN: apiKeySecret.secretArn,
      },
      logRetention: logs.RetentionDays.ONE_WEEK,
      tracing: lambda.Tracing.ACTIVE, // Enable X-Ray tracing
    });

    // Grant permissions to data generator
    apiKeySecret.grantRead(dataGeneratorFunction);
    fraudRulesTable.grantReadWriteData(dataGeneratorFunction);

    // CloudWatch Events Rule to trigger data generator every 5 minutes
    const dataGeneratorRule = new events.Rule(this, 'DataGeneratorRule', {
      ruleName: 'transaction-data-generator-schedule',
      description: 'Trigger transaction data generator every 5 minutes',
      schedule: events.Schedule.rate(cdk.Duration.minutes(5)),
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
      timeout: cdk.Duration.minutes(10),
      memorySize: 512,
      environment: {
        API_GATEWAY_URL: api.url,
        API_KEY_SECRET_ARN: apiKeySecret.secretArn,
        SEED_MODE: 'true',
      },
      logRetention: logs.RetentionDays.ONE_WEEK,
      tracing: lambda.Tracing.ACTIVE,
    });

    apiKeySecret.grantRead(seedDataFunction);
    fraudRulesTable.grantReadWriteData(seedDataFunction);

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

    new cdk.CfnOutput(this, 'ApiKeySecretArn', {
      value: apiKeySecret.secretArn,
      description: 'API Key Secret ARN (use AWS CLI to get the actual key value)'
    });
  }
}
