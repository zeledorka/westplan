"""Backend-only provider selection; configuring a key does not activate a provider."""
import os
from pathlib import Path
from dotenv import load_dotenv
from .gemini import GeminiTransport, PlannerError


def configured_transport(env_file=Path(__file__).resolve().parents[3]/'.env'):
    if env_file is not None:
        load_dotenv(env_file, override=False)
    provider = os.getenv('PLANNER_PROVIDER', 'openrouter')
    if provider == 'gemini':
        return GeminiTransport(env_file=None)
    if provider == 'openrouter':
        from .openrouter import OpenRouterTransport
        return OpenRouterTransport(env_file=None)
    raise PlannerError('planner_configuration', 'PLANNER_PROVIDER must be gemini or openrouter.', 503)
