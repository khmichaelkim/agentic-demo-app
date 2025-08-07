# Test Suite Documentation

This directory contains unit tests for the Agentic Demo App - a serverless transaction processing system with fraud detection using Python Lambda functions.

## Test Structure

```
tests/
├── README.md                       # This file
├── requirements.txt                # Python test dependencies
├── unit/                           # Unit tests (mocked dependencies)
│   ├── test_fraud_detection.py     # Python Lambda fraud detection tests
│   ├── test_transaction_service.py # Python Lambda transaction service tests
│   └── test_data_generator.py      # Python Lambda data generator tests
└── integration/                    # Integration tests (real API calls)
    └── (to be added after deployment)
```

## Running Tests

### Prerequisites
- Python 3.11+ (to match Lambda runtime)
- pip for installing test dependencies

### Unit Tests
Unit tests mock all AWS services and external dependencies:
```bash
npm run test:unit
```

### All Tests
```bash
npm test
```

### Test Coverage
```bash
npm run test:coverage
```

### Manual Python Testing
You can also run tests directly with pytest:
```bash
cd tests
pip install -r requirements.txt
pytest unit/ -v
```

## Test Categories

### Unit Tests
- **Fraud Detection Lambda**: Tests risk scoring logic, DynamoDB interactions, error handling
- **Transaction Service**: Tests Lambda function, business rules, AWS service integration  
- **Data Generator**: Tests transaction data generation, API calls, error scenarios

All unit tests use mocking to isolate the functions being tested and avoid external dependencies.

## Test Data

### Unit Test Data
Unit tests use mocked data that covers:
- Valid and invalid transaction requests
- Different risk levels and fraud scenarios
- Error conditions (network failures, service unavailability)
- Edge cases (boundary values, malformed data)

## Environment Setup

### Prerequisites
1. Python 3.11+ 
2. pip package manager
3. AWS CLI configured with appropriate permissions (for integration tests)
4. CDK CLI installed globally
5. Deployed CDK stack (for integration tests)

## Test Configuration

Tests use pytest with the following configuration:
- **Test Environment**: Python unittest with mocking
- **Test Pattern**: `test_*.py`
- **Coverage**: Includes `lambda/` directories
- **Mocking**: AWS SDK, HTTP requests, and X-Ray tracing

## Troubleshooting

### Common Issues

1. **Import errors**: Module not found issues
   - Ensure you're running tests from the correct directory
   - Check that Python path includes the lambda directories
   - Verify all test dependencies are installed

2. **Mocking issues**: Tests failing due to unmocked services
   - Check that all AWS services are properly mocked in setUp()
   - Verify X-Ray SDK mocking is correct
   - Ensure external HTTP calls are mocked

3. **Test dependencies**: Missing packages
   - Run `pip install -r tests/requirements.txt`
   - Check Python version compatibility (3.11+)

### Debugging Tips

1. **Run specific test file**:
   ```bash
   cd tests && python -m pytest unit/test_fraud_detection.py -v
   ```

2. **Run specific test method**:
   ```bash
   cd tests && python -m pytest unit/test_fraud_detection.py::TestFraudDetection::test_low_risk_transaction -v
   ```

3. **Run with detailed output**:
   ```bash
   cd tests && python -m pytest unit/ -v -s
   ```

4. **Debug with coverage**:
   ```bash
   cd tests && python -m pytest --cov=../lambda --cov-report=html
   ```

## Architecture Testing Strategy

This test suite follows a comprehensive testing pyramid:

```
     /\
    /  \  Unit Tests (Fast, Isolated, Python)
   /____\
  /      \
 / Integration \ (Slower, Real AWS Services)  
/______________\
```

- **Unit Tests**: Fast, isolated, high coverage using Python unittest and mocking
- **Integration Tests**: End-to-end testing with real AWS services (to be added)
- **Manual Testing**: Complex scenarios, UI validation (not automated)

## Test Coverage Goals

- **Lambda Functions**: 90%+ line coverage
- **Error Handling**: All error paths tested  
- **Business Logic**: All fraud detection rules and transaction processing logic
- **AWS Integration**: All AWS service interactions mocked and tested

## Adding New Tests

### Unit Test Guidelines
1. Use Python unittest framework
2. Mock all external dependencies (AWS SDK, HTTP calls)
3. Test both success and failure scenarios
4. Use descriptive test method names
5. Include setUp() and tearDown() methods for proper cleanup
6. Group related functionality in test classes

### Example Test Structure
```python
class TestNewFeature(unittest.TestCase):
    
    def setUp(self):
        # Mock AWS services and environment variables
        pass
    
    def tearDown(self):
        # Clean up mocks
        pass
    
    def test_successful_operation(self):
        # Test the happy path
        pass
    
    def test_error_handling(self):
        # Test error scenarios
        pass
```

The goal is to catch most issues in unit tests while ensuring the system works correctly end-to-end with integration tests after deployment.