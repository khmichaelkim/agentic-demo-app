import unittest
import json
import os
import sys
from unittest.mock import patch, MagicMock

class TestFraudDetection(unittest.TestCase):
    """Simple integration tests for fraud detection service."""
    
    def setUp(self):
        """Set up test environment."""
        self.env_vars = {
            'FRAUD_RULES_TABLE': 'test-fraud-rules-table',
            'TRANSACTIONS_TABLE': 'test-transactions-table',
            'RISK_THRESHOLD_HIGH': '80',
            'RISK_THRESHOLD_MEDIUM': '50'
        }
        
        # Mock AWS services
        self.mock_dynamodb = MagicMock()
        
        # Add lambda path
        self.fraud_path = os.path.join(os.path.dirname(__file__), '../../lambda/fraud-detection')
        sys.path.insert(0, self.fraud_path)
    
    def tearDown(self):
        """Clean up after tests."""
        sys.path.remove(self.fraud_path)
        if 'index' in sys.modules:
            del sys.modules['index']
    
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_fraud_detection_low_risk(self, mock_patch_all, mock_resource):
        """Test fraud detection with low-risk transaction."""
        with patch.dict(os.environ, self.env_vars):
            # Mock DynamoDB
            mock_resource.return_value = self.mock_dynamodb
            mock_table = MagicMock()
            self.mock_dynamodb.Table.return_value = mock_table
            
            # Mock fraud rules scan (empty rules - will use defaults)
            mock_table.scan.return_value = {'Items': []}
            
            # Mock user transaction history (no recent transactions)
            mock_table.query.return_value = {'Items': []}
            
            # Import after mocking
            import index as fraud_index
            
            # Create low-risk transaction
            transaction = {
                'transactionId': 'test-txn-123',
                'userId': 'user-001',
                'amount': 50.00,  # Low amount
                'currency': 'USD',
                'location': 'US-CA',  # Safe location
                'correlationId': 'test-corr-123'
            }
            
            # Call handler
            response = fraud_index.handler(transaction, {})
            
            # Verify low-risk response
            self.assertEqual(response['transactionId'], 'test-txn-123')
            self.assertEqual(response['userId'], 'user-001')
            self.assertEqual(response['riskLevel'], 'LOW')
            self.assertLess(response['riskScore'], 50)  # Below medium threshold
            self.assertEqual(response['correlationId'], 'test-corr-123')
    
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_fraud_detection_high_risk(self, mock_patch_all, mock_resource):
        """Test fraud detection with high-risk transaction."""
        with patch.dict(os.environ, self.env_vars):
            # Mock DynamoDB
            mock_resource.return_value = self.mock_dynamodb
            mock_table = MagicMock()
            self.mock_dynamodb.Table.return_value = mock_table
            
            # Mock fraud rules scan (empty rules - will use defaults)
            mock_table.scan.return_value = {'Items': []}
            
            # Mock user transaction history (many recent transactions for velocity)
            recent_transactions = [{'transactionId': f'txn-{i}'} for i in range(15)]
            mock_table.query.return_value = {'Items': recent_transactions}
            
            # Import after mocking
            import index as fraud_index
            
            # Create high-risk transaction
            transaction = {
                'transactionId': 'test-txn-456',
                'userId': 'user-002',
                'amount': 15000.00,  # High amount
                'currency': 'USD',
                'location': 'XX-XX',  # Risky location
                'correlationId': 'test-corr-456'
            }
            
            # Call handler
            response = fraud_index.handler(transaction, {})
            
            # Verify high-risk response
            self.assertEqual(response['transactionId'], 'test-txn-456')
            self.assertEqual(response['userId'], 'user-002')
            self.assertEqual(response['riskLevel'], 'HIGH')
            self.assertGreaterEqual(response['riskScore'], 80)  # Above high threshold
            self.assertEqual(response['correlationId'], 'test-corr-456')
    
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_fraud_detection_missing_fields(self, mock_patch_all, mock_resource):
        """Test fraud detection with missing required fields."""
        with patch.dict(os.environ, self.env_vars):
            # Mock DynamoDB
            mock_resource.return_value = self.mock_dynamodb
            
            # Import after mocking
            import index as fraud_index
            
            # Create transaction with missing required fields
            invalid_transaction = {
                'transactionId': 'test-txn-789',
                # Missing userId and amount
                'currency': 'USD'
            }
            
            # Call handler - should raise ValueError
            with self.assertRaises(ValueError) as context:
                fraud_index.handler(invalid_transaction, {})
            
            self.assertIn('Missing required transaction field', str(context.exception))

if __name__ == '__main__':
    unittest.main()