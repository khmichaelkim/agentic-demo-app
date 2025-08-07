import unittest
import os
import sys
from unittest.mock import patch, MagicMock

class TestFraudAlgorithms(unittest.TestCase):
    """Unit tests for fraud detection algorithms."""
    
    def setUp(self):
        """Set up test environment."""
        self.env_vars = {
            'FRAUD_RULES_TABLE': 'test-fraud-rules-table',
            'TRANSACTIONS_TABLE': 'test-transactions-table',
            'RISK_THRESHOLD_HIGH': '80',
            'RISK_THRESHOLD_MEDIUM': '50'
        }
        
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
    def test_check_amount_rules_with_default_rules(self, mock_patch_all, mock_resource):
        """Test amount-based fraud checking with default rules."""
        with patch.dict(os.environ, self.env_vars):
            import index as fraud_index
            
            # Test high amount (over $10k)
            high_amount_transaction = {'amount': 15000}
            score = fraud_index.check_amount_rules(high_amount_transaction, [])
            self.assertEqual(score, 40)  # Default high penalty
            
            # Test medium amount ($5k-$10k)
            medium_amount_transaction = {'amount': 7000}
            score = fraud_index.check_amount_rules(medium_amount_transaction, [])
            self.assertEqual(score, 20)  # Default medium penalty
            
            # Test low amount ($1k-$5k)
            low_amount_transaction = {'amount': 2000}
            score = fraud_index.check_amount_rules(low_amount_transaction, [])
            self.assertEqual(score, 10)  # Default low penalty
            
            # Test very low amount (under $1k)
            very_low_transaction = {'amount': 500}
            score = fraud_index.check_amount_rules(very_low_transaction, [])
            self.assertEqual(score, 0)  # No penalty
    
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_check_amount_rules_with_custom_rules(self, mock_patch_all, mock_resource):
        """Test amount-based fraud checking with custom rules."""
        with patch.dict(os.environ, self.env_vars):
            import index as fraud_index
            
            # Custom rules
            custom_rules = [
                {'ruleType': 'AMOUNT_THRESHOLD', 'threshold': 1000, 'penalty': 25},
                {'ruleType': 'AMOUNT_THRESHOLD', 'threshold': 500, 'penalty': 15}
            ]
            
            # Test transaction that triggers both rules
            transaction = {'amount': 1500}
            score = fraud_index.check_amount_rules(transaction, custom_rules)
            self.assertEqual(score, 40)  # 25 + 15 = 40
            
            # Test transaction that triggers one rule
            transaction = {'amount': 750}
            score = fraud_index.check_amount_rules(transaction, custom_rules)
            self.assertEqual(score, 15)  # Only second rule triggered
    
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_check_location_rules_default(self, mock_patch_all, mock_resource):
        """Test location-based fraud checking with default rules."""
        with patch.dict(os.environ, self.env_vars):
            import index as fraud_index
            
            # Test high-risk location
            risky_transaction = {'location': 'XX-XX'}
            score = fraud_index.check_location_rules(risky_transaction, [])
            self.assertEqual(score, 20)  # Default high-risk penalty
            
            # Test unknown location
            unknown_transaction = {'location': 'UNKNOWN'}
            score = fraud_index.check_location_rules(unknown_transaction, [])
            self.assertEqual(score, 20)  # Default high-risk penalty
            
            # Test safe location
            safe_transaction = {'location': 'US-CA'}
            score = fraud_index.check_location_rules(safe_transaction, [])
            self.assertEqual(score, 0)  # No penalty
    
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_determine_risk_level(self, mock_patch_all, mock_resource):
        """Test risk level determination based on score."""
        with patch.dict(os.environ, self.env_vars):
            import index as fraud_index
            
            # Test high risk (>= 80)
            self.assertEqual(fraud_index.determine_risk_level(85), 'HIGH')
            self.assertEqual(fraud_index.determine_risk_level(80), 'HIGH')
            
            # Test medium risk (50-79)
            self.assertEqual(fraud_index.determine_risk_level(65), 'MEDIUM')
            self.assertEqual(fraud_index.determine_risk_level(50), 'MEDIUM')
            
            # Test low risk (< 50)
            self.assertEqual(fraud_index.determine_risk_level(30), 'LOW')
            self.assertEqual(fraud_index.determine_risk_level(0), 'LOW')
    
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_get_default_fraud_rules(self, mock_patch_all, mock_resource):
        """Test default fraud rules structure."""
        with patch.dict(os.environ, self.env_vars):
            import index as fraud_index
            
            default_rules = fraud_index.get_default_fraud_rules()
            
            # Should have multiple rules
            self.assertGreater(len(default_rules), 0)
            
            # Each rule should have required fields
            for rule in default_rules:
                self.assertIn('ruleId', rule)
                self.assertIn('ruleType', rule)
                self.assertIn('threshold', rule)
                self.assertIn('penalty', rule)
                self.assertIn('action', rule)
                self.assertIn('priority', rule)
            
            # Should have different types of rules
            rule_types = [rule['ruleType'] for rule in default_rules]
            self.assertIn('AMOUNT_THRESHOLD', rule_types)
            self.assertIn('VELOCITY_CHECK', rule_types)
    
    @patch('boto3.resource')
    @patch('aws_xray_sdk.core.patch_all')
    def test_calculate_risk_score_caps_at_100(self, mock_patch_all, mock_resource):
        """Test that risk score is capped at 100."""
        with patch.dict(os.environ, self.env_vars):
            # Mock DynamoDB
            mock_resource.return_value = MagicMock()
            mock_table = MagicMock()
            mock_resource.return_value.Table.return_value = mock_table
            mock_table.scan.return_value = {'Items': []}
            mock_table.query.return_value = {'Items': []}  # No velocity issues
            
            import index as fraud_index
            
            # Create extremely high-risk transaction
            extreme_transaction = {
                'amount': 50000,    # Very high amount
                'location': 'XX-XX',  # Risky location
                'userId': 'test-user'
            }
            
            # Should cap at 100
            score = fraud_index.calculate_risk_score(extreme_transaction, [])
            self.assertLessEqual(score, 100)

if __name__ == '__main__':
    unittest.main()