#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AgenticDemoAppStack } from '../lib/agentic-demo-app-stack';
import { EcsFargateStack } from '../lib/ecs-fargate-stack';

const app = new cdk.App();

// Environment configuration for us-east-1
const envConfig = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: 'us-east-1'
};

// ECS Fargate stack for credit score service (deploy first)
const ecsStack = new EcsFargateStack(app, 'EcsFargateStack', {
  env: envConfig,
});

// Main serverless stack (now with correct ALB URL from ECS stack)
const mainStack = new AgenticDemoAppStack(app, 'AgenticDemoAppStack', {
  env: envConfig,
  creditScoreAlbUrl: ecsStack.creditScoreAlbUrl, // Pass ALB URL directly from ECS stack
});

// Restore dependency now that export conflict is resolved
mainStack.addDependency(ecsStack);