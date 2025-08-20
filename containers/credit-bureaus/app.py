import asyncio
import logging
import os
import random
import time
from typing import Dict

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

app = FastAPI(title="Credit Bureaus Service", version="1.0.0")

# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)
RequestsInstrumentor().instrument()

# Configuration from environment variables
FAILURE_RATE_EXPERIAN = float(os.getenv('FAILURE_RATE_EXPERIAN', '0.05'))
FAILURE_RATE_EQUIFAX = float(os.getenv('FAILURE_RATE_EQUIFAX', '0.03'))
FAILURE_RATE_TRANSUNION = float(os.getenv('FAILURE_RATE_TRANSUNION', '0.07'))
MIN_LATENCY_MS = int(os.getenv('MIN_LATENCY_MS', '100'))
MAX_LATENCY_MS = int(os.getenv('MAX_LATENCY_MS', '500'))

# Credit score ranges for different risk profiles
SCORE_RANGES = {
    'excellent': (750, 850),
    'good': (670, 749),
    'fair': (580, 669),
    'poor': (300, 579)
}

# Distribution of credit scores (weighted towards good/excellent)
SCORE_DISTRIBUTION = [
    ('excellent', 0.25),  # 25% excellent
    ('good', 0.45),       # 45% good
    ('fair', 0.20),       # 20% fair
    ('poor', 0.10)        # 10% poor
]


class UserScoreRequest(BaseModel):
    userId: str


class ScoreResponse(BaseModel):
    userId: str
    score: int
    bureau: str
    response_time_ms: float
    timestamp: str


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    service: str
    bureau_endpoints: list


def generate_user_score(user_id: str, bureau: str) -> int:
    """Generate a consistent score for a user from a specific bureau.
    
    Uses user_id as seed to ensure consistent scores across requests.
    """
    # Use user_id and bureau as seed for consistency
    seed_value = hash(f"{user_id}-{bureau}") % 1000000
    random.seed(seed_value)
    
    # Choose score category based on distribution
    rand_val = random.random()
    cumulative_prob = 0
    
    for category, probability in SCORE_DISTRIBUTION:
        cumulative_prob += probability
        if rand_val <= cumulative_prob:
            min_score, max_score = SCORE_RANGES[category]
            score = random.randint(min_score, max_score)
            return score
    
    # Fallback to fair range
    return random.randint(580, 669)


async def simulate_bureau_processing(bureau: str, user_id: str, failure_rate: float) -> ScoreResponse:
    """Simulate bureau processing with latency and potential failures."""
    start_time = time.time()
    
    # Get correlation ID from headers if available
    correlation_id = getattr(simulate_bureau_processing, 'correlation_id', 'unknown')
    
    # Simulate processing latency
    latency_ms = random.randint(MIN_LATENCY_MS, MAX_LATENCY_MS)
    await asyncio.sleep(latency_ms / 1000.0)
    
    # Simulate failures based on failure rate
    if random.random() < failure_rate:
        logger.warning(f"{bureau} bureau simulation failure - userId: {user_id}, correlation_id: {correlation_id}")
        raise HTTPException(
            status_code=503,
            detail=f"{bureau} service temporarily unavailable"
        )
    
    # Generate score
    score = generate_user_score(user_id, bureau)
    
    response_time_ms = (time.time() - start_time) * 1000
    
    logger.info(f"{bureau} bureau processed request - userId: {user_id}, score: {score}, "
               f"latency: {latency_ms}ms, correlation_id: {correlation_id}")
    
    return ScoreResponse(
        userId=user_id,
        score=score,
        bureau=bureau,
        response_time_ms=response_time_ms,
        timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    )


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for ECS health checks."""
    return HealthResponse(
        status="healthy",
        timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        service="credit-bureaus",
        bureau_endpoints=["/experian/score", "/equifax/score", "/transunion/score"]
    )


@app.post("/experian/score", response_model=ScoreResponse)
async def get_experian_score(request: UserScoreRequest, http_request: Request):
    """Mock Experian credit score endpoint."""
    # Store correlation ID for logging
    correlation_id = http_request.headers.get('X-Correlation-ID', 'unknown')
    simulate_bureau_processing.correlation_id = correlation_id
    
    logger.info(f"Experian score request - userId: {request.userId}, correlation_id: {correlation_id}")
    
    return await simulate_bureau_processing("experian", request.userId, FAILURE_RATE_EXPERIAN)


@app.post("/equifax/score", response_model=ScoreResponse)
async def get_equifax_score(request: UserScoreRequest, http_request: Request):
    """Mock Equifax credit score endpoint."""
    # Store correlation ID for logging
    correlation_id = http_request.headers.get('X-Correlation-ID', 'unknown')
    simulate_bureau_processing.correlation_id = correlation_id
    
    logger.info(f"Equifax score request - userId: {request.userId}, correlation_id: {correlation_id}")
    
    return await simulate_bureau_processing("equifax", request.userId, FAILURE_RATE_EQUIFAX)


@app.post("/transunion/score", response_model=ScoreResponse)
async def get_transunion_score(request: UserScoreRequest, http_request: Request):
    """Mock TransUnion credit score endpoint."""
    # Store correlation ID for logging
    correlation_id = http_request.headers.get('X-Correlation-ID', 'unknown')
    simulate_bureau_processing.correlation_id = correlation_id
    
    logger.info(f"TransUnion score request - userId: {request.userId}, correlation_id: {correlation_id}")
    
    return await simulate_bureau_processing("transunion", request.userId, FAILURE_RATE_TRANSUNION)


@app.get("/bureaus/config")
async def get_bureau_config():
    """Get current bureau configuration (for debugging/monitoring)."""
    return {
        "failure_rates": {
            "experian": FAILURE_RATE_EXPERIAN,
            "equifax": FAILURE_RATE_EQUIFAX,
            "transunion": FAILURE_RATE_TRANSUNION
        },
        "latency_range": {
            "min_ms": MIN_LATENCY_MS,
            "max_ms": MAX_LATENCY_MS
        },
        "score_ranges": SCORE_RANGES,
        "score_distribution": dict(SCORE_DISTRIBUTION)
    }


if __name__ == "__main__":
    # Start the FastAPI server
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8081,
        log_level="info",
        access_log=True
    )