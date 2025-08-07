import unittest
import os
import sys
from unittest.mock import patch

class TestLambdaSmoke(unittest.TestCase):
    """Smoke tests to verify Lambda functions can be imported and have basic structure."""
    
    def test_transaction_service_import(self):
        """Test that transaction service Lambda can be imported."""
        # Set up environment and path
        env_vars = {
            'DYNAMODB_TABLE_TRANSACTIONS': 'test-transactions-table',
            'DYNAMODB_TABLE_FRAUD_RULES': 'test-fraud-rules-table', 
            'SQS_QUEUE_URL': 'https://sqs.us-east-1.amazonaws.com/123456789012/test-queue',
            'LAMBDA_FRAUD_FUNCTION_NAME': 'test-fraud-function',
            'AWS_XRAY_TRACING_NAME': 'transaction-service'
        }
        
        transaction_path = os.path.join(os.path.dirname(__file__), '../../lambda/transaction-service')
        sys.path.insert(0, transaction_path)
        
        with patch.dict(os.environ, env_vars), \
             patch('boto3.client'), \
             patch('aws_xray_sdk.core.patch_all'):
            
            import index as transaction_index
            
            # Verify handler function exists
            self.assertTrue(hasattr(transaction_index, 'handler'))
            self.assertTrue(callable(transaction_index.handler))
            
            # Verify utility functions exist
            self.assertTrue(hasattr(transaction_index, 'validate_transaction_data'))
            self.assertTrue(hasattr(transaction_index, 'perform_fraud_check'))
            self.assertTrue(hasattr(transaction_index, 'store_transaction'))
            
        # Clean up
        sys.path.remove(transaction_path)
        if 'index' in sys.modules:
            del sys.modules['index']
    
    def test_fraud_detection_import(self):
        """Test that fraud detection Lambda can be imported."""
        # Set up environment and path
        env_vars = {
            'FRAUD_RULES_TABLE': 'test-fraud-rules-table',
            'TRANSACTIONS_TABLE': 'test-transactions-table',
            'RISK_THRESHOLD_HIGH': '80',
            'RISK_THRESHOLD_MEDIUM': '50',
            'AWS_XRAY_TRACING_NAME': 'fraud-detection-service'
        }
        
        fraud_path = os.path.join(os.path.dirname(__file__), '../../lambda/fraud-detection')
        sys.path.insert(0, fraud_path)
        
        with patch.dict(os.environ, env_vars), \
             patch('boto3.client'), \
             patch('aws_xray_sdk.core.patch_all'):
            
            import index as fraud_index
            
            # Verify handler function exists
            self.assertTrue(hasattr(fraud_index, 'handler'))
            self.assertTrue(callable(fraud_index.handler))
            
            # Verify core functions exist
            self.assertTrue(hasattr(fraud_index, 'get_fraud_rules'))
            self.assertTrue(hasattr(fraud_index, 'calculate_risk_score'))
            self.assertTrue(hasattr(fraud_index, 'check_amount_rules'))
            
        # Clean up
        sys.path.remove(fraud_path)
        if 'index' in sys.modules:
            del sys.modules['index']
    
    def test_data_generator_import(self):
        """Test that data generator Lambda can be imported."""
        # Set up environment and path
        env_vars = {
            'API_GATEWAY_URL': 'https://test-api.execute-api.us-east-1.amazonaws.com/prod',
            'API_KEY_SECRET_ARN': 'arn:aws:secretsmanager:us-east-1:123456789012:secret:test-api-key',
            'AWS_XRAY_TRACING_NAME': 'transaction-data-generator'
        }
        
        generator_path = os.path.join(os.path.dirname(__file__), '../../lambda/data-generator')
        sys.path.insert(0, generator_path)
        
        with patch.dict(os.environ, env_vars), \
             patch('boto3.client'), \
             patch('aws_xray_sdk.core.patch_all'):
            
            import index as generator_index
            
            # Verify handler functions exist
            self.assertTrue(hasattr(generator_index, 'handler'))
            self.assertTrue(callable(generator_index.handler))
            self.assertTrue(hasattr(generator_index, 'seed_handler'))
            self.assertTrue(callable(generator_index.seed_handler))
            
            # Verify utility functions exist
            self.assertTrue(hasattr(generator_index, 'get_api_key'))
            self.assertTrue(hasattr(generator_index, 'generate_transactions'))
            self.assertTrue(hasattr(generator_index, 'send_transaction'))
            
        # Clean up
        sys.path.remove(generator_path)
        if 'index' in sys.modules:
            del sys.modules['index']
    
    def test_lambda_requirements_exist(self):
        """Test that all Lambda functions have requirements.txt files."""
        base_path = os.path.join(os.path.dirname(__file__), '../../lambda')
        
        # Check transaction service requirements
        transaction_req = os.path.join(base_path, 'transaction-service/requirements.txt')
        self.assertTrue(os.path.exists(transaction_req), "Transaction service requirements.txt missing")
        
        # Check fraud detection requirements
        fraud_req = os.path.join(base_path, 'fraud-detection/requirements.txt')
        self.assertTrue(os.path.exists(fraud_req), "Fraud detection requirements.txt missing")
        
        # Check data generator requirements
        generator_req = os.path.join(base_path, 'data-generator/requirements.txt')
        self.assertTrue(os.path.exists(generator_req), "Data generator requirements.txt missing")
    
    def test_lambda_index_files_exist(self):
        """Test that all Lambda functions have index.py files."""
        base_path = os.path.join(os.path.dirname(__file__), '../../lambda')
        
        # Check transaction service
        transaction_index = os.path.join(base_path, 'transaction-service/index.py')
        self.assertTrue(os.path.exists(transaction_index), "Transaction service index.py missing")
        
        # Check fraud detection
        fraud_index = os.path.join(base_path, 'fraud-detection/index.py')
        self.assertTrue(os.path.exists(fraud_index), "Fraud detection index.py missing")
        
        # Check data generator
        generator_index = os.path.join(base_path, 'data-generator/index.py')
        self.assertTrue(os.path.exists(generator_index), "Data generator index.py missing")

if __name__ == '__main__':
    unittest.main()