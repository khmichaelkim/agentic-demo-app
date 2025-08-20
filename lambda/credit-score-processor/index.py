import json
import logging
import os
import boto3
import requests
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
dynamodb = boto3.resource('dynamodb')

# Environment variables
CREDIT_SCORE_ALB_URL = os.environ.get('CREDIT_SCORE_ALB_URL')
USER_CREDIT_SCORES_TABLE = os.environ.get('USER_CREDIT_SCORES_TABLE', 'UserCreditScoresTable')

# DynamoDB table
user_credit_scores_table = dynamodb.Table(USER_CREDIT_SCORES_TABLE)


def call_credit_score_service(user_id: str, transaction_id: str) -> Dict[str, Any]:
    """Call the ECS credit score service via ALB."""
    
    if not CREDIT_SCORE_ALB_URL:
        raise ValueError("CREDIT_SCORE_ALB_URL environment variable not set")
    
    url = f"{CREDIT_SCORE_ALB_URL}/score"
    payload = {"userId": user_id}
    
    headers = {
        'Content-Type': 'application/json',
        'X-Transaction-ID': transaction_id,
        'User-Agent': 'credit-score-trigger-lambda/1.0'
    }
    
    try:
        logger.info(f"Calling credit score service - userId: {user_id}, transactionId: {transaction_id}")
        
        # Make the request (no authentication needed for public ALB)
        response = requests.post(
            url,
            json=payload,  # Use json parameter instead of data for proper JSON serialization
            headers=headers,
            timeout=30  # 30 second timeout
        )
        
        if response.status_code == 200:
            credit_data = response.json()
            logger.info(f"Successfully got credit score - userId: {user_id}, "
                       f"score: {credit_data.get('aggregated_score')}, "
                       f"correlation_id: {credit_data.get('correlation_id')}")
            return credit_data
        else:
            error_msg = f"Credit score service returned status {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise Exception(error_msg)
            
    except requests.exceptions.Timeout:
        error_msg = f"Credit score service request timed out - userId: {user_id}"
        logger.error(error_msg)
        raise Exception(error_msg)
    except requests.exceptions.ConnectionError:
        error_msg = f"Failed to connect to credit score service - userId: {user_id}"
        logger.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error calling credit score service - userId: {user_id}, error: {str(e)}"
        logger.error(error_msg)
        raise


def store_credit_score(user_id: str, transaction_id: str, credit_data: Dict[str, Any]) -> None:
    """Store credit score data in DynamoDB."""
    
    timestamp = datetime.now(timezone.utc).isoformat()
    
    try:
        # Prepare item for DynamoDB with proper type conversion
        item = {
            'userId': user_id,
            'timestamp': timestamp,
            'transactionId': transaction_id,
            'creditScore': credit_data['aggregated_score'],
            'bureauScores': credit_data.get('bureau_scores', []),
            'correlationId': credit_data.get('correlation_id'),
            'totalResponseTimeMs': Decimal(str(credit_data.get('total_response_time_ms', 0))),
            'processedAt': timestamp,
            'source': 'ecs-credit-score-service'
        }
        
        # Convert bureau scores to DynamoDB format if they exist
        if 'bureau_scores' in credit_data:
            formatted_bureau_scores = []
            for bureau_score in credit_data['bureau_scores']:
                formatted_score = {
                    'bureau': bureau_score.get('bureau'),
                    'score': bureau_score.get('score'),
                    'responseTimeMs': Decimal(str(bureau_score.get('response_time_ms', 0)))
                }
                formatted_bureau_scores.append(formatted_score)
            item['bureauScores'] = formatted_bureau_scores
        
        # Store in DynamoDB
        user_credit_scores_table.put_item(Item=item)
        
        logger.info(f"Successfully stored credit score - userId: {user_id}, "
                   f"transactionId: {transaction_id}, score: {credit_data['aggregated_score']}")
        
    except Exception as e:
        error_msg = f"Failed to store credit score - userId: {user_id}, transactionId: {transaction_id}, error: {str(e)}"
        logger.error(error_msg)
        raise


def process_sqs_record(record: Dict[str, Any]) -> None:
    """Process a single SQS record."""
    
    try:
        # Parse SQS message body
        body = json.loads(record['body'])
        user_id = body.get('userId')
        transaction_id = body.get('transactionId')
        
        if not user_id:
            raise ValueError("Missing userId in SQS message")
        if not transaction_id:
            raise ValueError("Missing transactionId in SQS message")
        
        logger.info(f"Processing credit score request - userId: {user_id}, transactionId: {transaction_id}")
        
        # Call credit score service
        credit_data = call_credit_score_service(user_id, transaction_id)
        
        # Store results in DynamoDB
        store_credit_score(user_id, transaction_id, credit_data)
        
        logger.info(f"Successfully processed credit score request - userId: {user_id}, transactionId: {transaction_id}")
        
    except Exception as e:
        logger.error(f"Failed to process SQS record: {str(e)}")
        # Let the error propagate so SQS can handle retries/DLQ
        raise


def handler(event, context):
    """Lambda handler for SQS trigger."""
    
    logger.info(f"Processing {len(event['Records'])} SQS records")
    
    successful_records = 0
    failed_records = 0
    
    for record in event['Records']:
        try:
            process_sqs_record(record)
            successful_records += 1
        except Exception as e:
            failed_records += 1
            logger.error(f"Failed to process record: {str(e)}")
            # Continue processing other records
    
    logger.info(f"Processed records - success: {successful_records}, failed: {failed_records}")
    
    # Return response
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': f'Processed {successful_records} records successfully, {failed_records} failed',
            'successful_records': successful_records,
            'failed_records': failed_records
        })
    }