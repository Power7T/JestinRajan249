"""
OpenRouter Integration for HostAI
Drop-in replacement for direct Claude API calls with cost optimization
"""

import httpx
import os
import json
import logging
from typing import Optional, Dict, List
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ============================================================================
# Model Selection by Task Type
# ============================================================================

class ModelRouter:
    """Smart model selection based on task complexity"""

    # Available models on OpenRouter
    MODELS = {
        # Tier 1: Complex reasoning (most expensive, highest quality)
        "sonnet": "anthropic/claude-3-5-sonnet-20241022",
        "gpt4o": "openai/gpt-4-turbo",

        # Tier 2: Medium complexity (balanced)
        "llama70b": "meta-llama/llama-3.1-70b-instruct",
        "mistral-large": "mistralai/mistral-large-2407",

        # Tier 3: Simple tasks (cheap, fast)
        "llama8b": "meta-llama/llama-3.1-8b-instruct",
        "qwen72b": "qwen/qwen-2.5-72b-instruct",

        # Tier 4: Ultra-cheap for high-volume
        "qwen7b": "qwen/qwen-2.5-7b-instruct",
    }

    # Pricing per 1M tokens (input/output)
    PRICING = {
        "sonnet": {"input": 3.00, "output": 15.00},
        "gpt4o": {"input": 5.00, "output": 15.00},
        "llama70b": {"input": 0.27, "output": 0.81},
        "mistral-large": {"input": 0.27, "output": 0.81},
        "llama8b": {"input": 0.05, "output": 0.15},
        "qwen72b": {"input": 0.14, "output": 0.28},
        "qwen7b": {"input": 0.07, "output": 0.14},
    }

    @staticmethod
    def select_model(
        agent_type: str,
        task_type: str,
        complexity: str = "medium"
    ) -> tuple[str, str]:
        """
        Select best model for agent task

        Returns: (model_key, model_id)
        """

        # Routing rules
        routing = {
            "ceo": {
                "daily_analysis": "sonnet",      # Complex reasoning needed
                "budget_allocation": "sonnet",
                "forecasting": "sonnet",
                "escalation": "llama70b",        # Medium complexity
            },
            "sales": {
                "email_generation": "llama70b",   # Good personalization
                "lead_scoring": "llama70b",
                "deal_analysis": "sonnet",        # Complex negotiation
                "customer_retention": "llama70b",
            },
            "ops": {
                "capacity_check": "llama8b",      # Simple threshold
                "cost_analysis": "qwen72b",
                "scaling_decision": "llama70b",   # Medium complexity
                "security_audit": "llama70b",
            },
            "support": {
                "ticket_categorization": "llama8b",  # Simple classification
                "faq_lookup": "qwen72b",             # Fast lookup
                "escalation_decision": "llama70b",   # Medium
                "customer_health_check": "qwen72b",
            },
            "marketing": {
                "blog_post": "llama70b",          # Good content
                "social_media": "qwen72b",        # Fast & cheap
                "seo_analysis": "llama70b",
                "competitor_analysis": "sonnet",  # Complex reasoning
            }
        }

        # Override based on explicit complexity
        complexity_overrides = {
            "simple": "llama8b",
            "cheap": "qwen7b",
            "medium": "llama70b",
            "complex": "sonnet",
        }

        if complexity != "auto":
            model_key = complexity_overrides[complexity]
        else:
            model_key = routing.get(agent_type, {}).get(task_type, "llama70b")

        model_id = ModelRouter.MODELS[model_key]
        return model_key, model_id

    @staticmethod
    def get_cost(model_key: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for API call"""

        pricing = ModelRouter.PRICING[model_key]
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost


# ============================================================================
# OpenRouter Client
# ============================================================================

class OpenRouterClient:
    """Main client for OpenRouter API calls"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or OPENROUTER_API_KEY
        self.base_url = OPENROUTER_BASE_URL

        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set in environment")

    async def call(
        self,
        model_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        system_prompt: str = None
    ) -> dict:
        """
        Make API call to OpenRouter

        Returns: {
            "content": response text,
            "model": model used,
            "input_tokens": tokens used,
            "output_tokens": tokens generated,
            "cost": cost in USD
        }
        """

        if system_prompt:
            messages = [
                {"role": "system", "content": system_prompt}
            ] + messages

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "HTTP-Referer": "https://hostai.dev",
                    "X-Title": "HostAI Business Agent",
                },
                json={
                    "model": model_id,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
            )

        if response.status_code != 200:
            log.error(f"OpenRouter API error: {response.text}")
            raise Exception(f"OpenRouter API error: {response.status_code}")

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        input_tokens = data["usage"]["prompt_tokens"]
        output_tokens = data["usage"]["completion_tokens"]

        # Find model key to get pricing
        model_key = None
        for key, model_id_check in ModelRouter.MODELS.items():
            if model_id_check == model_id:
                model_key = key
                break

        cost = ModelRouter.get_cost(model_key, input_tokens, output_tokens) if model_key else 0

        return {
            "content": content,
            "model": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
        }


# ============================================================================
# Agent-Specific Wrappers
# ============================================================================

async def ceo_analysis(metrics: dict) -> dict:
    """CEO Agent: Daily business analysis"""

    client = OpenRouterClient()
    model_key, model_id = ModelRouter.select_model("ceo", "daily_analysis")

    messages = [
        {
            "role": "user",
            "content": f"""Analyze these business metrics and provide strategic insights:

Revenue: ${metrics['revenue']}
Churn: {metrics['churn_rate']}%
New customers: {metrics['new_customers']}
Server utilization: {metrics['server_util']}%

Provide:
1. Summary of performance
2. Budget allocation for Sales/Ops/Support
3. Top 3 risks and opportunities
4. 30-day forecast"""
        }
    ]

    result = await client.call(model_id, messages)
    result["agent"] = "CEO"
    result["task"] = "daily_analysis"
    return result


async def sales_outreach(customer_data: dict) -> dict:
    """Sales Agent: Generate personalized outreach email"""

    client = OpenRouterClient()
    model_key, model_id = ModelRouter.select_model("sales", "email_generation")

    system = """You are a professional sales agent. Write personalized,
    compelling emails that feel personal, not generic. Keep under 150 words."""

    messages = [
        {
            "role": "user",
            "content": f"""Draft a sales email for this customer:

Name: {customer_data['name']}
Company: {customer_data['company']}
Current usage: {customer_data['usage']}% of plan
Monthly spend: ${customer_data['monthly_spend']}
Last login: {customer_data['last_login']} days ago

Subject: Offer them an upgrade to a better plan based on their usage.
Make it personal, highlight their growth, explain the benefits."""
        }
    ]

    result = await client.call(model_id, messages, system_prompt=system)
    result["agent"] = "Sales"
    result["task"] = "email_generation"
    return result


async def ops_capacity_check(server_metrics: dict) -> dict:
    """Ops Agent: Simple capacity monitoring"""

    client = OpenRouterClient()
    model_key, model_id = ModelRouter.select_model("ops", "capacity_check")

    messages = [
        {
            "role": "user",
            "content": f"""Check server capacity status:

CPU: {server_metrics['cpu']}%
Memory: {server_metrics['memory']}%
Disk: {server_metrics['disk']}%

Respond with:
1. Status: HEALTHY / WARNING / CRITICAL
2. Action needed: NONE / MONITOR / SCALE / INVESTIGATE"""
        }
    ]

    result = await client.call(model_id, messages, max_tokens=100)
    result["agent"] = "Ops"
    result["task"] = "capacity_check"
    return result


async def support_ticket(ticket_data: dict) -> dict:
    """Support Agent: Handle support ticket"""

    client = OpenRouterClient()
    model_key, model_id = ModelRouter.select_model("support", "ticket_categorization")

    system = """You are a helpful support agent. Categorize the issue and provide
    a response if it's a common problem. Otherwise, recommend escalation."""

    messages = [
        {
            "role": "user",
            "content": f"""Support ticket:

Customer: {ticket_data['customer_name']}
Issue: {ticket_data['issue_text']}

Respond with:
1. Category: DNS / SSL / FTP / BILLING / OTHER
2. Can auto-resolve: YES / NO
3. Response or escalation recommendation"""
        }
    ]

    result = await client.call(model_id, messages, system_prompt=system, max_tokens=300)
    result["agent"] = "Support"
    result["task"] = "ticket_categorization"
    return result


# ============================================================================
# Cost Tracking
# ============================================================================

def log_api_usage(agent: str, task: str, model_key: str, tokens_in: int, tokens_out: int, cost: float):
    """Log API usage for cost tracking"""

    log.info(
        f"Agent={agent} | Task={task} | Model={model_key} | "
        f"Tokens={tokens_in + tokens_out} | Cost=${cost:.4f}"
    )

    # TODO: Store in database for analytics
    # db.add(APIUsageLog(
    #     agent=agent,
    #     task=task,
    #     model=model_key,
    #     input_tokens=tokens_in,
    #     output_tokens=tokens_out,
    #     cost_usd=cost,
    #     created_at=datetime.now(timezone.utc)
    # ))


# ============================================================================
# Usage Examples
# ============================================================================

if __name__ == "__main__":
    import asyncio

    async def examples():
        """Run example API calls"""

        # Example 1: CEO Daily Analysis
        print("\n=== CEO Agent ===")
        result = await ceo_analysis({
            "revenue": 8234,
            "churn_rate": 1.5,
            "new_customers": 3,
            "server_util": 71,
        })
        print(f"Model: {result['model']}")
        print(f"Cost: ${result['cost']:.4f}")
        print(f"Response: {result['content'][:200]}...\n")

        # Example 2: Sales Outreach
        print("=== Sales Agent ===")
        result = await sales_outreach({
            "name": "John Smith",
            "company": "TechCorp",
            "usage": 65,
            "monthly_spend": 99,
            "last_login": 3,
        })
        print(f"Model: {result['model']}")
        print(f"Cost: ${result['cost']:.4f}")
        print(f"Response: {result['content']}\n")

        # Example 3: Ops Capacity Check
        print("=== Ops Agent ===")
        result = await ops_capacity_check({
            "cpu": 72,
            "memory": 68,
            "disk": 45,
        })
        print(f"Model: {result['model']}")
        print(f"Cost: ${result['cost']:.4f}")
        print(f"Response: {result['content']}\n")

    asyncio.run(examples())
