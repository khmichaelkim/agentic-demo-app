import asyncio
import logging
import os
import time
import uuid
from typing import Dict, List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

# OpenTelemetry imports
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize OpenTelemetry tracing
otlp_endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4318')
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)

# Configure OTLP exporter
otlp_exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
span_processor = BatchSpanProcessor(otlp_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

app = FastAPI(title="Credit Score Service", version="1.0.0")

# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)
RequestsInstrumentor().instrument()

# Configuration from environment variables
BUREAU_SERVICE_URL = os.getenv('BUREAU_SERVICE_URL', 'http://credit-bureaus.credit-score.local:8081')
RETRY_ATTEMPTS = int(os.getenv('RETRY_ATTEMPTS', '3'))
RETRY_DELAY_MS = int(os.getenv('RETRY_DELAY_MS', '500'))


class CreditScoreRequest(BaseModel):
    userId: str


class BureauScore(BaseModel):
    bureau: str
    score: int
    response_time_ms: float


class CreditScoreResponse(BaseModel):
    userId: str
    aggregated_score: int
    bureau_scores: List[BureauScore]
    correlation_id: str
    total_response_time_ms: float


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    service: str


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for ALB and ECS health checks."""
    return HealthResponse(
        status="healthy",
        timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        service="credit-score-service"
    )


@app.post("/score", response_model=CreditScoreResponse)
async def get_credit_score(request: CreditScoreRequest, http_request: Request):
    """Get aggregated credit score from multiple bureaus."""
    correlation_id = str(uuid.uuid4())
    start_time = time.time()
    
    logger.info(f"Processing credit score request - userId: {request.userId}, correlation_id: {correlation_id}")
    
    # Bureau endpoints to call
    bureaus = [
        {'name': 'experian', 'endpoint': '/experian/score'},
        {'name': 'equifax', 'endpoint': '/equifax/score'},
        {'name': 'transunion', 'endpoint': '/transunion/score'}
    ]
    
    bureau_scores = []
    
    # Call each bureau
    for bureau_info in bureaus:
        bureau_name = bureau_info['name']
        endpoint = bureau_info['endpoint']
        
        try:
            bureau_score = await call_bureau_with_retry(
                bureau_name, endpoint, request.userId, correlation_id
            )
            if bureau_score:
                bureau_scores.append(bureau_score)
        except Exception as e:
            logger.error(f"Failed to get score from {bureau_name} after all retries - correlation_id: {correlation_id}, error: {str(e)}")
            # Continue with other bureaus even if one fails
    
    if not bureau_scores:
        logger.error(f"No bureau scores available - correlation_id: {correlation_id}")
        raise HTTPException(
            status_code=503, 
            detail=f"Unable to retrieve credit scores from any bureau - correlation_id: {correlation_id}"
        )
    
    # Calculate aggregated score (average of available scores)
    total_score = sum(score.score for score in bureau_scores)
    aggregated_score = total_score // len(bureau_scores)
    
    total_response_time = (time.time() - start_time) * 1000  # Convert to milliseconds
    
    response = CreditScoreResponse(
        userId=request.userId,
        aggregated_score=aggregated_score,
        bureau_scores=bureau_scores,
        correlation_id=correlation_id,
        total_response_time_ms=total_response_time
    )
    
    logger.info(f"Credit score processed - userId: {request.userId}, score: {aggregated_score}, "
               f"bureaus: {len(bureau_scores)}/{len(bureaus)}, correlation_id: {correlation_id}")
    
    return response


async def call_bureau_with_retry(bureau_name: str, endpoint: str, user_id: str, correlation_id: str) -> Optional[BureauScore]:
    """Call bureau endpoint with retry logic."""
    url = f"{BUREAU_SERVICE_URL}{endpoint}"
    payload = {"userId": user_id}
    headers = {
        "Content-Type": "application/json",
        "X-Correlation-ID": correlation_id
    }
    
    for attempt in range(RETRY_ATTEMPTS):
        try:
            start_time = time.time()
            
            logger.info(f"Calling {bureau_name} bureau - attempt {attempt + 1}/{RETRY_ATTEMPTS}, "
                       f"correlation_id: {correlation_id}")
            
            response = requests.post(
                url, 
                json=payload, 
                headers=headers, 
                timeout=10  # 10 second timeout
            )
            
            response_time_ms = (time.time() - start_time) * 1000
            
            if response.status_code == 200:
                data = response.json()
                bureau_score = BureauScore(
                    bureau=bureau_name,
                    score=data['score'],
                    response_time_ms=response_time_ms
                )
                
                logger.info(f"Successfully got score from {bureau_name} - score: {data['score']}, "
                           f"response_time: {response_time_ms:.2f}ms, correlation_id: {correlation_id}")
                
                return bureau_score
            
            else:
                logger.warning(f"{bureau_name} returned status {response.status_code} - attempt {attempt + 1}/{RETRY_ATTEMPTS}, "
                              f"correlation_id: {correlation_id}")
                
                if attempt < RETRY_ATTEMPTS - 1:  # Don't sleep on last attempt
                    await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
                    
        except requests.exceptions.Timeout:
            logger.warning(f"{bureau_name} request timed out - attempt {attempt + 1}/{RETRY_ATTEMPTS}, "
                          f"correlation_id: {correlation_id}")
        except requests.exceptions.ConnectionError:
            logger.warning(f"{bureau_name} connection error - attempt {attempt + 1}/{RETRY_ATTEMPTS}, "
                          f"correlation_id: {correlation_id}")
        except Exception as e:
            logger.error(f"Unexpected error calling {bureau_name} - attempt {attempt + 1}/{RETRY_ATTEMPTS}, "
                        f"correlation_id: {correlation_id}, error: {str(e)}")
        
        if attempt < RETRY_ATTEMPTS - 1:  # Don't sleep on last attempt
            time.sleep(RETRY_DELAY_MS / 1000.0)
    
    # All attempts failed
    logger.error(f"All attempts failed for {bureau_name} - correlation_id: {correlation_id}")
    return None


if __name__ == "__main__":
    import asyncio
    
    # Start the FastAPI server
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        access_log=True
    )