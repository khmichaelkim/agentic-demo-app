"""
Predefined demo scenarios for transaction traffic generation.
These scenarios are designed for compelling live demonstrations.
"""

from typing import Dict, Any, List
from datetime import datetime

# Default scenario configuration
DEFAULT_SCENARIO = {
    'scenario': 'normal',
    'tps': 200,  # Increased to 200 to generate sustained throttling pressure
    'duration': 300,
    'pattern': {
        'type': 'steady'
    },
    'description': 'Normal steady traffic'
}

# Predefined demo scenarios optimized for live presentations
DEMO_SCENARIOS = {
    'normal': {
        'scenario': 'normal',
        'tps': 200,  # Increased to 200 to generate sustained throttling pressure
        'duration': 300,
        'pattern': {'type': 'steady'},
        'description': 'Normal steady traffic - baseline for demos',
        'transaction_mix': {
            'low_value': 0.7,    # 70% under $100
            'medium_value': 0.25, # 25% $100-$1000  
            'high_value': 0.05   # 5% over $1000
        },
        'special_features': {
            'reset_wcu': True  # Temporarily reset WCU to 1 for elevated traffic demo
        }
    },
    
    'black_friday_rush': {
        'scenario': 'black_friday_rush',
        'tps': 25,
        'duration': 180,
        'pattern': {
            'type': 'curve',
            'phases': [
                {'duration': 30, 'tps': 5, 'name': 'normal'},      # Normal baseline
                {'duration': 60, 'tps': 25, 'name': 'building'},   # Building up  
                {'duration': 60, 'tps': 25, 'name': 'peak'},       # Peak surge
                {'duration': 30, 'tps': 5, 'name': 'cooldown'}     # Cool down
            ]
        },
        'description': 'Black Friday traffic surge with realistic e-commerce patterns',
        'demo_callouts': {
            '30': 'Traffic spike begins - notice the TPS increase',
            '90': 'Peak shopping period - system under high load',
            '150': 'Traffic normalizing - watch the graceful recovery'
        },
        'transaction_mix': {
            'low_value': 0.3,    # Higher value transactions during sales
            'medium_value': 0.5,
            'high_value': 0.2
        }
    },
    
    'demo_throttling': {
        'scenario': 'demo_throttling',
        'tps': 5,  # Normal TPS (not used during burst)
        'duration': 20,  # Quick 20-second demo
        'pattern': {
            'type': 'steady'  # Simple pattern
        },
        'description': 'Simple DynamoDB throttling demo - 500 concurrent transactions',
        'demo_callouts': {
            '0': '🔴 BURST: 500 concurrent transactions hitting 1 WCU limit',
            '5': '📊 Throttling in progress - observe 503 errors',
            '15': '✅ Demo complete - system recovered'
        },
        'transaction_mix': {
            'low_value': 0.9,    # Keep transactions simple and fast
            'medium_value': 0.08,
            'high_value': 0.02
        },
        'special_features': {
            'burst_mode': True,  # Simple burst mode
            'burst_size': 500,   # 500 concurrent transactions
            'reset_wcu': True    # Reset WCU to 1 before burst
        }
    },
    
    'gradual_increase': {
        'scenario': 'gradual_increase', 
        'tps': 20,
        'duration': 240,
        'pattern': {
            'type': 'ramp',
            'tps_start': 5,
            'tps_end': 20,
            'ramp_time': 120,  # 2 minutes to ramp up
            'sustain_time': 60, # Hold at peak for 1 minute
            'ramp_down_time': 60 # 1 minute to return to normal
        },
        'description': 'Gradual traffic increase to demonstrate auto-scaling behaviors',
        'demo_callouts': {
            '60': 'Traffic steadily increasing - watch system adaptation',
            '120': 'Peak load reached - system at full capacity',
            '180': 'Load decreasing - resources scaling back down'
        }
    },
    
    'fraud_spike': {
        'scenario': 'fraud_spike',
        'tps': 12,
        'duration': 120,
        'pattern': {'type': 'steady'},
        'description': 'High-risk transaction spike to demonstrate fraud detection',
        'transaction_mix': {
            'low_value': 0.2,
            'medium_value': 0.3,
            'high_value': 0.5    # 50% high-value for fraud triggers
        },
        'special_features': {
            'high_risk_locations': 0.3,  # 30% from risky locations
            'velocity_patterns': True,   # Rapid transactions from same users
            'suspicious_amounts': True   # Round numbers, repeated amounts
        },
        'demo_callouts': {
            '30': 'Fraud patterns emerging - notice increased risk scores',
            '60': 'High declination rate - fraud system working',
            '90': 'Patterns detected - see the fraud metrics spike'
        }
    },
    
    'perfect_storm': {
        'scenario': 'perfect_storm',
        'tps': 20,
        'duration': 180,
        'pattern': {
            'type': 'curve',
            'phases': [
                {'duration': 30, 'tps': 8, 'name': 'buildup'},
                {'duration': 90, 'tps': 20, 'name': 'chaos'},
                {'duration': 60, 'tps': 8, 'name': 'recovery'}
            ]
        },
        'description': 'Multiple failure modes: high traffic + fraud spike + system stress',
        'transaction_mix': {
            'low_value': 0.3,
            'medium_value': 0.4,
            'high_value': 0.3
        },
        'special_features': {
            'high_risk_locations': 0.4,
            'velocity_patterns': True,
            'concentrate_users': True,
            'predictable_patterns': False  # Chaos mode
        },
        'demo_callouts': {
            '30': 'Multiple stressors beginning - traffic and fraud spike',
            '60': 'System under severe stress - multiple failure modes active', 
            '120': 'Peak chaos - demonstrating system resilience',
            '150': 'Recovery beginning - watch graceful degradation patterns'
        }
    }
}

def get_scenario_config(scenario_name: str) -> Dict[str, Any]:
    """Get configuration for a specific scenario"""
    return DEMO_SCENARIOS.get(scenario_name, DEFAULT_SCENARIO).copy()

def list_available_scenarios() -> List[Dict[str, Any]]:
    """Get list of all available scenarios with metadata"""
    scenarios = []
    for name, config in DEMO_SCENARIOS.items():
        scenarios.append({
            'name': name,
            'description': config.get('description', ''),
            'tps': config.get('tps'),
            'duration': config.get('duration'),
            'pattern_type': config.get('pattern', {}).get('type'),
            'has_demo_callouts': bool(config.get('demo_callouts')),
            'complexity': len(config.get('special_features', {}))
        })
    return scenarios

def calculate_current_tps(scenario_config: Dict[str, Any], elapsed_seconds: int) -> int:
    """Calculate current TPS based on scenario pattern and elapsed time"""
    pattern = scenario_config.get('pattern', {})
    pattern_type = pattern.get('type', 'steady')
    
    if pattern_type == 'steady':
        return scenario_config.get('tps', 5)
    
    elif pattern_type == 'curve':
        phases = pattern.get('phases', [])
        current_time = 0
        
        for phase in phases:
            phase_duration = phase.get('duration', 0)
            if current_time <= elapsed_seconds < current_time + phase_duration:
                return phase.get('tps', 5)
            current_time += phase_duration
        
        # If past all phases, return last phase TPS
        return phases[-1].get('tps', 5) if phases else 5
    
    elif pattern_type == 'ramp':
        tps_start = pattern.get('tps_start', 5)
        tps_end = pattern.get('tps_end', 20)
        ramp_time = pattern.get('ramp_time', 120)
        sustain_time = pattern.get('sustain_time', 60)
        ramp_down_time = pattern.get('ramp_down_time', 60)
        
        if elapsed_seconds <= ramp_time:
            # Ramp up phase
            progress = elapsed_seconds / ramp_time
            return int(tps_start + (tps_end - tps_start) * progress)
        elif elapsed_seconds <= ramp_time + sustain_time:
            # Sustain phase
            return tps_end
        elif elapsed_seconds <= ramp_time + sustain_time + ramp_down_time:
            # Ramp down phase
            ramp_down_elapsed = elapsed_seconds - ramp_time - sustain_time
            progress = ramp_down_elapsed / ramp_down_time
            return int(tps_end - (tps_end - tps_start) * progress)
        else:
            # Back to start
            return tps_start
    
    return scenario_config.get('tps', 5)

def get_demo_callout(scenario_config: Dict[str, Any], elapsed_seconds: int) -> str:
    """Get demo callout for current time if available"""
    callouts = scenario_config.get('demo_callouts', {})
    
    # Find callout for current time (within 30 second window)
    for time_str, callout in callouts.items():
        callout_time = int(time_str)
        if callout_time <= elapsed_seconds < callout_time + 30:
            return callout
    
    return None

def get_transaction_mix(scenario_config: Dict[str, Any]) -> Dict[str, float]:
    """Get transaction value distribution for scenario"""
    return scenario_config.get('transaction_mix', {
        'low_value': 0.7,
        'medium_value': 0.25,
        'high_value': 0.05
    })

def should_use_special_features(scenario_config: Dict[str, Any]) -> Dict[str, bool]:
    """Get special feature flags for transaction generation"""
    return scenario_config.get('special_features', {})