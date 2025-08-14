import json
import random
import os
import logging
from datetime import datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    """
    Card Verification Service Lambda Handler
    Randomly fails ~5% of the time to simulate card verification issues
    Uses IAM authentication via Function URL
    """
    # Handle OPTIONS requests for CORS
    if event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': '*',
                'Access-Control-Allow-Headers': '*',
                'Access-Control-Allow-Credentials': 'true',
                'Access-Control-Max-Age': '86400'
            },
            'body': ''
        }
    
    correlation_id = event.get('correlationId', 'unknown')
    if event.get('body'):
        try:
            body = json.loads(event['body'])
            correlation_id = body.get('correlationId', correlation_id)
        except:
            pass
    
    # Log request with IAM authentication info
    logger.info(f"[{correlation_id}] Card verification request received (IAM authenticated)")
    
    # Log the IAM principal if available in the request context
    request_context = event.get('requestContext', {})
    if 'authorizer' in request_context and 'iam' in request_context['authorizer']:
        iam_info = request_context['authorizer']['iam']
        logger.info(f"[{correlation_id}] IAM Principal: {iam_info.get('principalOrgId', 'N/A')}")
    
    logger.debug(f"Event: {json.dumps(event)}")
    
    # Get failure rate from environment variable or use default 5%
    failure_rate = float(os.environ.get('FAILURE_RATE', '0.05'))
    
    # Prepare standard headers for all responses
    headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': '*',
        'Access-Control-Allow-Headers': '*',
        'Access-Control-Allow-Credentials': 'true'
    }
    
    # Simulate random failures at specified rate
    if random.random() < failure_rate:
        logger.error(f"[{correlation_id}] Card verification failed - service unavailable")
        
        return {
            'statusCode': 503,
            'headers': headers,
            'body': json.dumps({
                'error': 'Service unavailable',
                'timestamp': datetime.utcnow().isoformat(),
                'correlationId': correlation_id
            })
        }
    
    # Normal successful response
    logger.info(f"[{correlation_id}] Card verification successful")
    
    return {
        'statusCode': 200,
        'headers': headers,
        'body': json.dumps({
            'verified': True,
            'timestamp': datetime.utcnow().isoformat(),
            'correlationId': correlation_id
        })
    }