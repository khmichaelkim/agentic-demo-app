import unittest
import json
import os
import sys
from unittest.mock import patch, MagicMock

class TestAPIEndpoints(unittest.TestCase):
    """Simple integration tests for API endpoints."""
    
    def setUp(self):
        """Set up test environment."""
        self.env_vars = {
            'DYNAMODB_TABLE_TRANSACTIONS': 'test-transactions-table',
            'DYNAMODB_TABLE_FRAUD_RULES': 'test-fraud-rules-table',
            'SQS_QUEUE_URL': 'https://sqs.us-east-1.amazonaws.com/123456789012/test-queue',
            'LAMBDA_FRAUD_FUNCTION_NAME': 'test-fraud-function',
            'AWS_XRAY_TRACING_NAME': 'transaction-service'
        }
        
        # Mock AWS services
        self.mock_dynamodb = MagicMock()
        self.mock_lambda_client = MagicMock()
        self.mock_sqs_client = MagicMock()
        self.mock_cloudwatch_client = MagicMock()
        
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
    def test_health_endpoint(self, mock_patch_all, mock_resource, mock_client):
        """Test health endpoint returns success."""
        with patch.dict(os.environ, self.env_vars):
            # Import after mocking
            import index as transaction_index
            
            # Create health check event
            event = {
                'requestContext': {'http': {'method': 'GET', 'path': '/health'}},
                'body': None
            }
            
            # Call handler
            response = transaction_index.handler(event, {})
            
            # Verify response
            self.assertEqual(response['statusCode'], 200)
            body = json.loads(response['body'])
            self.assertEqual(body['status'], 'healthy')
            self.assertEqual(body['service'], 'transaction-processing')
    
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_transaction_endpoint_valid_request(self, mock_patch_all, mock_resource, mock_client):
        """Test transaction endpoint with valid request."""
        with patch.dict(os.environ, self.env_vars):
            # Mock AWS clients
            mock_client.return_value = self.mock_lambda_client
            mock_resource.return_value = self.mock_dynamodb
            
            # Mock table
            mock_table = MagicMock()
            self.mock_dynamodb.Table.return_value = mock_table
            
            # Mock fraud detection response
            fraud_response = {
                'transactionId': 'test-txn-123',
                'userId': 'user-001',
                'riskScore': 30,
                'riskLevel': 'LOW',
                'timestamp': '2024-01-01T12:00:00Z'
            }
            
            # Mock Lambda invoke
            mock_invoke_response = {
                'Payload': MagicMock()
            }
            mock_invoke_response['Payload'].read.return_value = json.dumps(fraud_response).encode()
            self.mock_lambda_client.invoke.return_value = mock_invoke_response
            
            # Import after mocking
            import index as transaction_index
            
            # Create valid transaction request
            transaction_data = {
                'userId': 'user-001',
                'amount': 100.50,
                'currency': 'USD',
                'merchantId': 'merchant-123'
            }
            
            event = {
                'requestContext': {'http': {'method': 'POST', 'path': '/transactions'}},
                'body': json.dumps(transaction_data)
            }
            
            # Call handler
            response = transaction_index.handler(event, {})
            
            # Verify response
            self.assertEqual(response['statusCode'], 201)  # Should be approved
            body = json.loads(response['body'])
            self.assertIn('transactionId', body)
            self.assertEqual(body['status'], 'APPROVED')
            self.assertEqual(body['riskScore'], 'LOW')
            
            # Verify fraud check was called
            self.mock_lambda_client.invoke.assert_called_once()
            
            # Verify transaction was stored
            mock_table.put_item.assert_called_once()
    
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_transaction_endpoint_invalid_request(self, mock_patch_all, mock_resource, mock_client):
        """Test transaction endpoint with invalid request."""
        with patch.dict(os.environ, self.env_vars):
            # Mock AWS clients
            mock_client.return_value = self.mock_lambda_client
            mock_resource.return_value = self.mock_dynamodb
            
            # Import after mocking
            import index as transaction_index
            
            # Create invalid transaction request (missing required fields)
            invalid_data = {
                'userId': 'user-001',
                'amount': -100.50,  # Invalid negative amount
                'currency': 'US'    # Invalid currency (too short)
            }
            
            event = {
                'requestContext': {'http': {'method': 'POST', 'path': '/transactions'}},
                'body': json.dumps(invalid_data)
            }
            
            # Call handler
            response = transaction_index.handler(event, {})
            
            # Verify error response
            self.assertEqual(response['statusCode'], 400)
            body = json.loads(response['body'])
            self.assertEqual(body['error'], 'Invalid request')
            self.assertIn('correlationId', body)
    
    @patch('boto3.client')
    @patch('boto3.resource') 
    @patch('aws_xray_sdk.core.patch_all')
    def test_transaction_endpoint_high_risk_decline(self, mock_patch_all, mock_resource, mock_client):
        """Test transaction endpoint with high-risk transaction that gets declined."""
        with patch.dict(os.environ, self.env_vars):
            # Mock AWS clients
            mock_client.return_value = self.mock_lambda_client
            mock_resource.return_value = self.mock_dynamodb
            
            # Mock table
            mock_table = MagicMock()
            self.mock_dynamodb.Table.return_value = mock_table
            
            # Mock high-risk fraud detection response
            fraud_response = {
                'transactionId': 'test-txn-456',
                'userId': 'user-002',
                'riskScore': 85,  # High risk score
                'riskLevel': 'HIGH',
                'timestamp': '2024-01-01T12:00:00Z'
            }
            
            # Mock Lambda invoke
            mock_invoke_response = {
                'Payload': MagicMock()
            }
            mock_invoke_response['Payload'].read.return_value = json.dumps(fraud_response).encode()
            self.mock_lambda_client.invoke.return_value = mock_invoke_response
            
            # Import after mocking
            import index as transaction_index
            
            # Create high-value transaction request
            transaction_data = {
                'userId': 'user-002',
                'amount': 15000.00,  # High amount
                'currency': 'USD',
                'merchantId': 'merchant-456'
            }
            
            event = {
                'requestContext': {'http': {'method': 'POST', 'path': '/transactions'}},
                'body': json.dumps(transaction_data)
            }
            
            # Call handler
            response = transaction_index.handler(event, {})
            
            # Verify declined response
            self.assertEqual(response['statusCode'], 402)  # Should be declined
            body = json.loads(response['body'])
            self.assertIn('transactionId', body)
            self.assertEqual(body['status'], 'DECLINED')
            self.assertEqual(body['riskScore'], 'HIGH')
            self.assertIn('reason', body)
            self.assertEqual(body['reason'], 'High fraud risk detected')

if __name__ == '__main__':
    unittest.main()