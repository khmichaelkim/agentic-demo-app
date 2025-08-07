import unittest
import os
import sys
from unittest.mock import patch

class TestTransactionValidation(unittest.TestCase):
    """Unit tests for transaction validation logic."""
    
    def setUp(self):
        """Set up test environment."""
        self.env_vars = {
            'DYNAMODB_TABLE_TRANSACTIONS': 'test-transactions-table',
            'DYNAMODB_TABLE_FRAUD_RULES': 'test-fraud-rules-table',
            'SQS_QUEUE_URL': 'https://sqs.us-east-1.amazonaws.com/123456789012/test-queue',
            'LAMBDA_FRAUD_FUNCTION_NAME': 'test-fraud-function'
        }
        
        # Add lambda path
        self.transaction_path = os.path.join(os.path.dirname(__file__), '../../lambda/transaction-service')
        sys.path.insert(0, self.transaction_path)
    
    def tearDown(self):
        """Clean up after tests."""
        sys.path.remove(self.transaction_path)
        if 'index' in sys.modules:
            del sys.modules['index']
    
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_validate_transaction_data_valid(self, mock_patch_all, mock_resource, mock_client):
        """Test validation with valid transaction data."""
        with patch.dict(os.environ, self.env_vars):
            import index as transaction_index
            
            # Valid transaction data
            valid_data = {
                'userId': 'user-123',
                'amount': 100.50,
                'currency': 'USD',
                'merchantId': 'merchant-456'
            }
            
            is_valid, error_message = transaction_index.validate_transaction_data(valid_data)
            
            self.assertTrue(is_valid)
            self.assertIsNone(error_message)
            # Should set default location
            self.assertEqual(valid_data['location'], 'US-XX')
    
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_validate_transaction_data_missing_fields(self, mock_patch_all, mock_resource, mock_client):
        """Test validation with missing required fields."""
        with patch.dict(os.environ, self.env_vars):
            import index as transaction_index
            
            # Missing userId
            invalid_data = {
                'amount': 100.50,
                'currency': 'USD',
                'merchantId': 'merchant-456'
            }
            
            is_valid, error_message = transaction_index.validate_transaction_data(invalid_data)
            
            self.assertFalse(is_valid)
            self.assertEqual(error_message, 'Missing required field: userId')
    
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_validate_transaction_data_negative_amount(self, mock_patch_all, mock_resource, mock_client):
        """Test validation with negative amount."""
        with patch.dict(os.environ, self.env_vars):
            import index as transaction_index
            
            # Negative amount
            invalid_data = {
                'userId': 'user-123',
                'amount': -50.00,
                'currency': 'USD',
                'merchantId': 'merchant-456'
            }
            
            is_valid, error_message = transaction_index.validate_transaction_data(invalid_data)
            
            self.assertFalse(is_valid)
            self.assertEqual(error_message, 'Amount must be positive')
    
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_validate_transaction_data_invalid_currency(self, mock_patch_all, mock_resource, mock_client):
        """Test validation with invalid currency."""
        with patch.dict(os.environ, self.env_vars):
            import index as transaction_index
            
            # Invalid currency (too short)
            invalid_data = {
                'userId': 'user-123',
                'amount': 100.50,
                'currency': 'US',  # Should be 3 characters
                'merchantId': 'merchant-456'
            }
            
            is_valid, error_message = transaction_index.validate_transaction_data(invalid_data)
            
            self.assertFalse(is_valid)
            self.assertEqual(error_message, 'Currency must be 3 characters')
    
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_apply_business_rules_high_risk_decline(self, mock_patch_all, mock_resource, mock_client):
        """Test business rules decline high-risk transactions."""
        with patch.dict(os.environ, self.env_vars):
            import index as transaction_index
            
            transaction = {'amount': 1000}
            fraud_result = {'riskLevel': 'HIGH'}
            
            status = transaction_index.apply_business_rules(transaction, fraud_result)
            
            self.assertEqual(status, 'DECLINED')
    
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_apply_business_rules_medium_risk_high_amount_decline(self, mock_patch_all, mock_resource, mock_client):
        """Test business rules decline medium-risk high-amount transactions."""
        with patch.dict(os.environ, self.env_vars):
            import index as transaction_index
            
            transaction = {'amount': 6000}  # Above 5000 threshold
            fraud_result = {'riskLevel': 'MEDIUM'}
            
            status = transaction_index.apply_business_rules(transaction, fraud_result)
            
            self.assertEqual(status, 'DECLINED')
    
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_apply_business_rules_low_risk_approve(self, mock_patch_all, mock_resource, mock_client):
        """Test business rules approve low-risk transactions."""
        with patch.dict(os.environ, self.env_vars):
            import index as transaction_index
            
            transaction = {'amount': 100}
            fraud_result = {'riskLevel': 'LOW'}
            
            status = transaction_index.apply_business_rules(transaction, fraud_result)
            
            self.assertEqual(status, 'APPROVED')

if __name__ == '__main__':
    unittest.main()