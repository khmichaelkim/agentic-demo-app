# Demo Constants for Agentic Transaction Processing App
# Centralized configuration values for easy demo scenario tuning

# Demo User Pool - Consistent across all scenarios
DEMO_USERS = [
    'user-001', 'user-002', 'user-003', 'user-004', 'user-005',
    'user-006', 'user-007', 'user-008', 'user-009', 'user-010',
    'user-011', 'user-012', 'user-013', 'user-014', 'user-015'
]

# Concentrated user pool for velocity testing scenarios
VELOCITY_TEST_USERS = ['user-001', 'user-002', 'user-003']

# Sample merchants for realistic transactions
DEMO_MERCHANTS = [
    'amazon-retail', 'starbucks-cafe', 'shell-gas', 'walmart-store',
    'target-retail', 'mcdonalds-fast', 'whole-foods', 'costco-wholesale',
    'uber-rides', 'netflix-streaming', 'spotify-music', 'apple-store',
    'home-depot', 'bestbuy-tech', 'cvs-pharmacy'
]

# Geographic data for location-based scenarios
SAFE_LOCATIONS = ['US-CA', 'US-NY', 'US-TX', 'US-FL', 'EU-GB', 'EU-DE', 'CA-ON', 'AU-NSW']
HIGH_RISK_LOCATIONS = ['XX-XX', 'UNKNOWN', 'CN-XX', 'RU-XX']
SUPPORTED_CURRENCIES = ['USD', 'EUR', 'GBP', 'CAD', 'AUD']

# Transaction amount ranges for different risk scenarios
TRANSACTION_AMOUNTS = {
    'low_risk': {'min': 5.0, 'max': 99.99},
    'medium_risk': {'min': 100.0, 'max': 999.99},
    'high_risk': {'min': 1000.0, 'max': 4999.99},
    'very_high_risk': {'min': 5000.0, 'max': 20000.0}
}

# Fraud detection thresholds (default values, overridden by env vars)
DEFAULT_FRAUD_THRESHOLDS = {
    'risk_high': 80,
    'risk_medium': 50,
    'risk_low': 25
}

# Common demo scenario configurations
DEMO_SCENARIOS = {
    'normal': {
        'transaction_mix': {'low_value': 0.7, 'medium_value': 0.25, 'high_value': 0.05},
        'risk_location_probability': 0.02,
        'velocity_multiplier': 1
    },
    'fraud_demo': {
        'transaction_mix': {'low_value': 0.3, 'medium_value': 0.4, 'high_value': 0.3},
        'risk_location_probability': 0.15,
        'velocity_multiplier': 3
    },
    'throttling_demo': {
        'burst_size': 50,
        'burst_duration_seconds': 10,
        'concurrent_workers': 50
    },
    'bad_code_push': {
        'description': 'Rewards service without caching causing latency spikes',
        'cache_enabled': False,
        'rewards_query_delay_ms': 1500
    }
}

# Retry configuration defaults (for demo throttling scenarios)
DEFAULT_RETRY_CONFIG = {
    'max_retries': 0,  # Surface throttling immediately for demos
    'base_delay_ms': 0.01,
    'max_backoff_ms': 1000
}

# Demo timing constants
DEMO_TIMING = {
    'default_scenario_duration': 300,  # 5 minutes
    'data_generation_interval': 60,    # 1 minute
    'burst_cooldown': 30               # 30 seconds
}