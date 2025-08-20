import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as servicediscovery from 'aws-cdk-lib/aws-servicediscovery';
import { Construct } from 'constructs';

export interface EcsFargateStackProps extends cdk.StackProps {
  // Optional props for cross-stack references if needed
}

export class EcsFargateStack extends cdk.Stack {
  public readonly creditScoreAlbUrl: string;
  
  constructor(scope: Construct, id: string, props?: EcsFargateStackProps) {
    super(scope, id, props);

    // Use default VPC (no new VPC creation)
    const vpc = ec2.Vpc.fromLookup(this, 'DefaultVpc', {
      isDefault: true,
    });

    // ECS Cluster
    const cluster = new ecs.Cluster(this, 'CreditScoreCluster', {
      vpc,
      clusterName: 'credit-score-cluster',
      containerInsightsV2: ecs.ContainerInsights.ENHANCED, // Enable container insights v2 for monitoring
    });

    // Service Discovery Namespace for internal communication
    const namespace = new servicediscovery.PrivateDnsNamespace(this, 'CreditScoreNamespace', {
      name: 'credit-score.local',
      vpc,
    });

    // CloudWatch Log Groups
    const creditScoreLogGroup = new logs.LogGroup(this, 'CreditScoreLogGroup', {
      logGroupName: '/ecs/credit-score-service',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      retention: logs.RetentionDays.ONE_WEEK, // Short retention for demo
    });

    const creditBureausLogGroup = new logs.LogGroup(this, 'CreditBureausLogGroup', {
      logGroupName: '/ecs/credit-bureaus',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      retention: logs.RetentionDays.ONE_WEEK, // Short retention for demo
    });

    // Task Execution Role (required for Fargate)
    const taskExecutionRole = new iam.Role(this, 'EcsTaskExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });

    // Task Role for runtime permissions (includes ADOT permissions)
    const taskRole = new iam.Role(this, 'EcsTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchAgentServerPolicy'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AWSXRayDaemonWriteAccess'),
      ],
      inlinePolicies: {
        CloudWatchLogs: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'logs:CreateLogStream',
                'logs:PutLogEvents',
              ],
              resources: ['*'],
            }),
          ],
        }),
        ApplicationSignals: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'cloudwatch:PutMetricData',
                'ec2:DescribeVolumes',
                'ec2:DescribeTags',
                'logs:PutLogEvents',
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'xray:PutTraceSegments',
                'xray:PutTelemetryRecords',
                'ssm:GetParameter',
              ],
              resources: ['*'],
            }),
          ],
        }),
      },
    });

    // Credit Bureaus Task Definition (Internal Service)
    const creditBureausTaskDefinition = new ecs.FargateTaskDefinition(this, 'CreditBureausTaskDefinition', {
      memoryLimitMiB: 512,
      cpu: 256,
      executionRole: taskExecutionRole,
      taskRole: taskRole,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    // Add ADOT Collector sidecar for credit bureaus (Application Signals configuration)
    const adotCollectorCreditBureaus = creditBureausTaskDefinition.addContainer('adot-collector-credit-bureaus', {
      image: ecs.ContainerImage.fromRegistry('public.ecr.aws/aws-observability/aws-otel-collector:v0.40.0'),
      essential: false,
      memoryReservationMiB: 128,
      cpu: 64,
      environment: {
        AWS_REGION: this.region,
        OTEL_RESOURCE_ATTRIBUTES: 'service.name=credit-bureaus,deployment.environment=ecs',
      },
      portMappings: [
        { containerPort: 4317, protocol: ecs.Protocol.TCP }, // OTLP gRPC
        { containerPort: 4318, protocol: ecs.Protocol.TCP }, // OTLP HTTP
      ],
      // Use Application Signals config - no custom config file needed
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'adot-credit-bureaus',
        logGroup: creditBureausLogGroup,
      }),
    });

    creditBureausTaskDefinition.addContainer('credit-bureaus-container', {
      image: ecs.ContainerImage.fromAsset('./containers/credit-bureaus'),
      portMappings: [{ containerPort: 8081 }],
      environment: {
        FAILURE_RATE_EXPERIAN: '0.05',
        FAILURE_RATE_EQUIFAX: '0.03',
        FAILURE_RATE_TRANSUNION: '0.07',
        MIN_LATENCY_MS: '100',
        MAX_LATENCY_MS: '500',
        // ADOT Configuration for Application Signals
        OTEL_EXPORTER_OTLP_ENDPOINT: 'http://localhost:4318',
        OTEL_RESOURCE_ATTRIBUTES: 'service.name=credit-bureaus,deployment.environment=ecs,team.name=external-integrations,business.unit=financial-services,app=transaction-processor',
        OTEL_SERVICE_NAME: 'credit-bureaus',
        OTEL_TRACES_SAMPLER: 'always_on',
        OTEL_METRICS_EXPORTER: 'otlp',
        OTEL_LOGS_EXPORTER: 'otlp',
        PYTHONPATH: '/otel-auto-instrumentation-python/opentelemetry/instrumentation/auto_instrumentation:/otel-auto-instrumentation-python',
        OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED: 'true',
      },
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'credit-bureaus',
        logGroup: creditBureausLogGroup,
      }),
      healthCheck: {
        command: ['CMD-SHELL', 'curl -f http://localhost:8081/health || exit 1'],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(60),
      },
      // Container will wait for essential ADOT sidecar to start
    });

    // Security Groups for inter-service communication
    const creditBureausSecurityGroup = new ec2.SecurityGroup(this, 'CreditBureausSecurityGroup', {
      vpc,
      description: 'Security group for Credit Bureaus service',
      allowAllOutbound: true,
    });

    const creditScoreSecurityGroup = new ec2.SecurityGroup(this, 'CreditScoreSecurityGroup', {
      vpc,
      description: 'Security group for Credit Score service',
      allowAllOutbound: true,
    });

    // Allow Credit Score service to connect to Credit Bureaus service on port 8081
    creditBureausSecurityGroup.addIngressRule(
      creditScoreSecurityGroup,
      ec2.Port.tcp(8081),
      'Allow Credit Score service to access Credit Bureaus service'
    );

    // Allow ALB to connect to Credit Score service on port 8080
    creditScoreSecurityGroup.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(8080),
      'Allow ALB to access Credit Score service'
    );

    // Credit Bureaus Service with Service Discovery
    const creditBureausService = new ecs.FargateService(this, 'CreditBureausService', {
      cluster,
      taskDefinition: creditBureausTaskDefinition,
      desiredCount: 1,
      serviceName: 'credit-bureaus',
      cloudMapOptions: {
        cloudMapNamespace: namespace,
        name: 'credit-bureaus',
        dnsRecordType: servicediscovery.DnsRecordType.A,
      },
      assignPublicIp: true, // Fix ECR connectivity issue
      minHealthyPercent: 100, // Prevent task reduction during deployments
      securityGroups: [creditBureausSecurityGroup],
    });

    // Credit Score Service Task Definition (Public-facing via ALB)
    const creditScoreTaskDefinition = new ecs.FargateTaskDefinition(this, 'CreditScoreTaskDefinition', {
      memoryLimitMiB: 512,
      cpu: 256,
      executionRole: taskExecutionRole,
      taskRole: taskRole,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    // Add ADOT Collector sidecar for credit score service (Application Signals configuration)
    const adotCollectorCreditScore = creditScoreTaskDefinition.addContainer('adot-collector-credit-score', {
      image: ecs.ContainerImage.fromRegistry('public.ecr.aws/aws-observability/aws-otel-collector:v0.40.0'),
      essential: false,
      memoryReservationMiB: 128,
      cpu: 64,
      environment: {
        AWS_REGION: this.region,
        OTEL_RESOURCE_ATTRIBUTES: 'service.name=credit-score-service,deployment.environment=ecs',
      },
      portMappings: [
        { containerPort: 4317, protocol: ecs.Protocol.TCP }, // OTLP gRPC
        { containerPort: 4318, protocol: ecs.Protocol.TCP }, // OTLP HTTP
      ],
      // Use Application Signals config - no custom config file needed
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'adot-credit-score',
        logGroup: creditScoreLogGroup,
      }),
    });

    creditScoreTaskDefinition.addContainer('credit-score-container', {
      image: ecs.ContainerImage.fromAsset('./containers/credit-score-service'),
      portMappings: [{ containerPort: 8080 }],
      environment: {
        BUREAU_SERVICE_URL: 'http://credit-bureaus.credit-score.local:8081',
        RETRY_ATTEMPTS: '3',
        RETRY_DELAY_MS: '500',
        // ADOT Configuration for Application Signals
        OTEL_EXPORTER_OTLP_ENDPOINT: 'http://localhost:4318',
        OTEL_RESOURCE_ATTRIBUTES: 'service.name=credit-score-service,deployment.environment=ecs,team.name=data-enrichment,business.unit=financial-services,app=transaction-processor',
        OTEL_SERVICE_NAME: 'credit-score-service',
        OTEL_TRACES_SAMPLER: 'always_on',
        OTEL_METRICS_EXPORTER: 'otlp',
        OTEL_LOGS_EXPORTER: 'otlp',
        PYTHONPATH: '/otel-auto-instrumentation-python/opentelemetry/instrumentation/auto_instrumentation:/otel-auto-instrumentation-python',
        OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED: 'true',
      },
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'credit-score-service',
        logGroup: creditScoreLogGroup,
      }),
      healthCheck: {
        command: ['CMD-SHELL', 'curl -f http://localhost:8080/health || exit 1'],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(60),
      },
      // Container will wait for essential ADOT sidecar to start
    });

    // Credit Score Service (Public-facing)
    const creditScoreService = new ecs.FargateService(this, 'CreditScoreService', {
      cluster,
      taskDefinition: creditScoreTaskDefinition,
      desiredCount: 1,
      serviceName: 'credit-score-service',
      cloudMapOptions: {
        cloudMapNamespace: namespace,
        name: 'credit-score-service',
        dnsRecordType: servicediscovery.DnsRecordType.A,
      },
      assignPublicIp: true, // Fix ECR connectivity issue
      minHealthyPercent: 100, // Prevent task reduction during deployments
      securityGroups: [creditScoreSecurityGroup],
    });

    // Application Load Balancer for Credit Score Service
    const alb = new elbv2.ApplicationLoadBalancer(this, 'CreditScoreALBV4', {
      vpc,
      internetFacing: true, // Internet-facing ALB so Lambda can reach it from outside VPC
      loadBalancerName: 'credit-score-alb-v4',
    });

    // Target Group for Credit Score Service
    const targetGroup = new elbv2.ApplicationTargetGroup(this, 'CreditScoreTargetGroupV4', {
      port: 8080,
      protocol: elbv2.ApplicationProtocol.HTTP,
      vpc,
      targetType: elbv2.TargetType.IP,
      targetGroupName: 'credit-score-tg-v4', // Updated to v4 to avoid conflicts
      healthCheck: {
        enabled: true,
        healthyHttpCodes: '200',
        path: '/health',
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3,
      },
    });

    // Add ECS Service to Target Group
    creditScoreService.attachToApplicationTargetGroup(targetGroup);

    // ALB Listener
    const listener = alb.addListener('CreditScoreListener', {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      defaultTargetGroups: [targetGroup],
    });

    // Store ALB URL for cross-stack reference
    this.creditScoreAlbUrl = `http://${alb.loadBalancerDnsName}`;


    // Output Cluster ARN
    new cdk.CfnOutput(this, 'EcsClusterArn', {
      value: cluster.clusterArn,
      description: 'ECS Cluster ARN',
      exportName: 'EcsClusterArn',
    });

    // Tags for cost tracking
    cdk.Tags.of(this).add('Project', 'AgenticDemoApp');
    cdk.Tags.of(this).add('Component', 'CreditScoreService');
    cdk.Tags.of(this).add('Environment', 'Demo');
  }
}