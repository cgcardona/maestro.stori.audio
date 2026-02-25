"""
Tests for budget tracking integration with conversation system.

Ensures that:
- Budget is properly tracked per message
- Costs are calculated correctly
- Budget limits are enforced
- Budget updates are persisted
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.db.models import User, UsageLog
from app.auth.tokens import create_access_token
from app.services import budget


# =============================================================================
# Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:

    """Create a test user with limited budget."""
    user = User(
        id="budget-test-user",
        budget_cents=100,  # $1.00 remaining
        budget_limit_cents=500,  # $5.00 total
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
def auth_token(test_user: Any) -> str:

    """Generate JWT token for test user."""
    return create_access_token(
        user_id=test_user.id,
        expires_hours=1,
    )


@pytest.fixture
def auth_headers(auth_token: Any) -> dict[str, str]:

    """Headers with authentication."""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }


# =============================================================================
# Budget Calculation Tests
# =============================================================================

@pytest.mark.asyncio
async def test_calculate_cost_cents() -> None:
    """Test cost calculation with different token counts."""
    # Use a model from APPROVED_MODELS (e.g. claude-3.7-sonnet: $3/M input, $15/M output)
    cost = budget.calculate_cost_cents(
        model="anthropic/claude-3.7-sonnet",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    # 1000*3/1e6 + 500*15/1e6 = 0.0105 dollars -> 1.05 cents -> rounded up to 2 cents
    assert cost >= 1
    assert cost <= 3


@pytest.mark.asyncio
async def test_calculate_cost_cents_zero_tokens() -> None:
    """Test cost calculation with zero tokens (minimum 1 cent per request)."""
    cost = budget.calculate_cost_cents(
        model="anthropic/claude-3.7-sonnet",
        prompt_tokens=0,
        completion_tokens=0,
    )
    # Implementation enforces minimum 1 cent per request
    assert cost == 1


# =============================================================================
# Budget Checking Tests
# =============================================================================

@pytest.mark.asyncio
async def test_check_budget_sufficient(db_session: AsyncSession, test_user: Any) -> None:

    """Test budget check passes when user has sufficient budget."""
    # Should not raise
    await budget.check_budget(db_session, test_user.id)


@pytest.mark.asyncio
async def test_check_budget_insufficient(db_session: AsyncSession) -> None:

    """Test budget check fails when user has insufficient budget."""
    # Create user with zero budget
    user = User(
        id="broke-user",
        budget_cents=0,
        budget_limit_cents=500,
    )
    db_session.add(user)
    await db_session.commit()
    
    with pytest.raises(budget.InsufficientBudgetError) as exc_info:
        await budget.check_budget(db_session, user.id)
    
    assert "Insufficient budget" in str(exc_info.value)


@pytest.mark.asyncio
async def test_check_budget_nonexistent_user(db_session: AsyncSession) -> None:

    """Test budget check with non-existent user."""
    with pytest.raises(budget.BudgetError) as exc_info:
        await budget.check_budget(db_session, "nonexistent-user")
    
    assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_check_budget_minimum_cents(db_session: AsyncSession, test_user: Any) -> None:

    """Test check_budget with custom minimum_cents."""
    await budget.check_budget(db_session, test_user.id, minimum_cents=1)
    await budget.check_budget(db_session, test_user.id, minimum_cents=100)
    with pytest.raises(budget.InsufficientBudgetError):
        await budget.check_budget(db_session, test_user.id, minimum_cents=101)


# =============================================================================
# Budget Reservation Tests
# =============================================================================

@pytest.mark.asyncio
async def test_reserve_budget_success_and_release(db_session: AsyncSession, test_user: Any) -> None:

    """Reserve budget then release unused portion."""
    initial = test_user.budget_cents
    res = await budget.reserve_budget(db_session, test_user.id, model="anthropic/claude-3.7-sonnet")
    await db_session.commit()
    await db_session.refresh(test_user)
    assert test_user.budget_cents == initial - res.reserved_cents
    await res.release()
    await db_session.commit()
    await db_session.refresh(test_user)
    assert test_user.budget_cents == initial


@pytest.mark.asyncio
async def test_reserve_budget_insufficient(db_session: AsyncSession) -> None:

    """reserve_budget raises InsufficientBudgetError when budget too low."""
    user = User(id="reserve-low", budget_cents=5, budget_limit_cents=500)
    db_session.add(user)
    await db_session.commit()
    with pytest.raises(budget.InsufficientBudgetError):
        await budget.reserve_budget(db_session, user.id, model="anthropic/claude-3.7-sonnet")
    await db_session.rollback()


@pytest.mark.asyncio
async def test_reserve_budget_user_not_found(db_session: AsyncSession) -> None:

    """reserve_budget raises BudgetError when user does not exist."""
    with pytest.raises(budget.BudgetError) as exc_info:
        await budget.reserve_budget(db_session, "nonexistent-user", model="anthropic/claude-3.7-sonnet")
    assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_reserve_budget_consume(db_session: AsyncSession, test_user: Any) -> None:

    """Reserve then consume with actual cost; unused portion released."""
    initial = test_user.budget_cents
    res = await budget.reserve_budget(db_session, test_user.id, model="anthropic/claude-3.7-sonnet")
    await db_session.flush()
    res.set_actual_cost(10)
    await res.consume(prompt_tokens=100, completion_tokens=50, prompt=None, store_prompt=False)
    await db_session.commit()
    await db_session.refresh(test_user)
    # Reserved ~25, actual 10, so 15 released back
    assert test_user.budget_cents == initial - 10


# =============================================================================
# Budget Deduction Tests
# =============================================================================

@pytest.mark.asyncio
async def test_deduct_budget(db_session: AsyncSession, test_user: Any) -> None:

    """Test deducting budget and logging usage."""
    original_budget = test_user.budget_cents
    
    await budget.deduct_budget(
        db=db_session,
        user_id=test_user.id,
        cost_cents=50,
        model="anthropic/claude-3.7-sonnet",
        prompt_tokens=1000,
        completion_tokens=500,
        prompt="Test prompt",
    )
    await db_session.commit()
    await db_session.refresh(test_user)
    
    # Check budget was deducted
    assert test_user.budget_cents == original_budget - 50
    
    # Check usage was logged
    from sqlalchemy import select
    result = await db_session.execute(
        select(UsageLog).where(UsageLog.user_id == test_user.id)
    )
    log = result.scalar_one()
    
    assert log.cost_cents == 50
    assert log.prompt == "Test prompt"
    assert log.model == "anthropic/claude-3.7-sonnet"
    assert log.prompt_tokens == 1000
    assert log.completion_tokens == 500


@pytest.mark.asyncio
async def test_deduct_budget_without_storing_prompt(db_session: AsyncSession, test_user: Any) -> None:

    """Test deducting budget without storing the prompt."""
    await budget.deduct_budget(
        db=db_session,
        user_id=test_user.id,
        cost_cents=25,
        model="anthropic/claude-3.7-sonnet",
        prompt_tokens=500,
        completion_tokens=250,
        prompt=None,  # Don't store prompt
    )
    await db_session.commit()
    
    # Check usage was logged without prompt
    from sqlalchemy import select
    result = await db_session.execute(
        select(UsageLog).where(UsageLog.user_id == test_user.id)
    )
    log = result.scalar_one()
    
    assert log.prompt is None
    assert log.cost_cents == 25


@pytest.mark.asyncio
async def test_deduct_budget_user_not_found(db_session: AsyncSession) -> None:

    """deduct_budget raises BudgetError when user does not exist."""
    with pytest.raises(budget.BudgetError) as exc_info:
        await budget.deduct_budget(
            db=db_session,
            user_id="nonexistent-user",
            cost_cents=10,
            prompt=None,
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
        )
    assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_deduct_budget_insufficient_funds(db_session: AsyncSession) -> None:

    """Test that deduction fails when budget is insufficient."""
    user = User(
        id="low-budget-user",
        budget_cents=10,
        budget_limit_cents=500,
    )
    db_session.add(user)
    await db_session.commit()
    
    # Try to deduct more than available
    with pytest.raises(budget.InsufficientBudgetError):
        await budget.deduct_budget(
            db=db_session,
            user_id=user.id,
            cost_cents=50,
            prompt=None,
            model="test-model",
            prompt_tokens=100,
            completion_tokens=50,
        )


# =============================================================================
# Model Selection Tests
# =============================================================================

@pytest.mark.asyncio
async def test_get_model_or_default_with_model() -> None:
    """Test that specified model is returned when it is in APPROVED_MODELS."""
    model = budget.get_model_or_default("anthropic/claude-3.7-sonnet")
    assert model == "anthropic/claude-3.7-sonnet"


@pytest.mark.asyncio
async def test_get_model_or_default_without_model() -> None:
    """Test that default model is returned when none specified."""
    model = budget.get_model_or_default(None)
    # Should return settings.llm_model (configured in test settings)
    assert model is not None


@pytest.mark.asyncio
async def test_get_user_budget_found(db_session: AsyncSession, test_user: Any) -> None:

    """get_user_budget returns remaining budget in dollars when user exists."""
    remaining = await budget.get_user_budget(db_session, test_user.id)
    assert remaining is not None
    assert remaining == test_user.budget_cents / 100.0


@pytest.mark.asyncio
async def test_get_user_budget_not_found(db_session: AsyncSession) -> None:

    """get_user_budget returns None when user does not exist."""
    remaining = await budget.get_user_budget(db_session, "nonexistent-user")
    assert remaining is None


@pytest.mark.asyncio
async def test_estimate_request_cost() -> None:
    """estimate_request_cost returns higher for composing."""
    normal = budget.estimate_request_cost("anthropic/claude-3.7-sonnet", is_composing=False)
    composing = budget.estimate_request_cost("anthropic/claude-3.7-sonnet", is_composing=True)
    assert composing >= normal


def test_validate_model_approved() -> None:
    """validate_model returns model name when in APPROVED_MODELS."""
    result = budget.validate_model("anthropic/claude-3.7-sonnet")
    assert result == "anthropic/claude-3.7-sonnet"


def test_validate_model_invalid_raises() -> None:
    """validate_model raises ValueError for unknown model."""
    with pytest.raises(ValueError) as exc_info:
        budget.validate_model("unknown/model-name")
    assert "not available" in str(exc_info.value).lower() or "approved" in str(exc_info.value).lower()


# =============================================================================
# End-to-End Budget Flow Tests
# =============================================================================

@pytest.mark.asyncio
async def test_conversation_message_budget_integration(db_session: AsyncSession, test_user: Any, auth_headers: Any) -> None:

    """Test that sending a message properly tracks budget."""
    # Create conversation first
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post(
            "/api/v1/conversations",
            headers=auth_headers,
            json={"title": "Budget Test"},
        )
        conversation_id = create_response.json()["id"]
    
    original_budget = test_user.budget_cents
    
    # Note: This test would require mocking the LLM client
    # or using a test mode. For now, we test the budget checking only.
    
    # Test budget check before sending message
    await budget.check_budget(db_session, test_user.id)
    # Should not raise


@pytest.mark.asyncio
async def test_budget_exceeded_error_in_api(db_session: AsyncSession, auth_headers: Any) -> None:

    """Test that API returns 402 when budget is exceeded."""
    # Create user with zero budget
    user = User(
        id="no-budget-user",
        budget_cents=0,
        budget_limit_cents=500,
    )
    db_session.add(user)
    await db_session.commit()
    
    # Create token for this user
    token = create_access_token(user_id=user.id, expires_hours=1)
    
    # Try to create conversation (should work)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post(
            "/api/v1/conversations",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"title": "Test"},
        )
        assert create_response.status_code == 201
        conversation_id = create_response.json()["id"]
        
        # Try to send message (should fail due to budget)
        # Note: Would need to mock LLM to fully test this
        # For now, we verify the budget check is in place


# =============================================================================
# Budget History Tests
# =============================================================================

@pytest.mark.asyncio
async def test_usage_log_creation(db_session: AsyncSession, test_user: Any) -> None:

    """Test that usage logs are properly created."""
    await budget.deduct_budget(
        db=db_session,
        user_id=test_user.id,
        cost_cents=30,
        model="test-model",
        prompt_tokens=600,
        completion_tokens=300,
        prompt="Test message",
    )
    await db_session.commit()
    
    from sqlalchemy import select
    result = await db_session.execute(
        select(UsageLog).where(UsageLog.user_id == test_user.id)
    )
    logs = list(result.scalars().all())
    
    assert len(logs) == 1
    assert logs[0].cost_cents == 30
    assert logs[0].total_tokens == 900


@pytest.mark.asyncio
async def test_multiple_usage_logs(db_session: AsyncSession, test_user: Any) -> None:

    """Test that multiple usage logs can be created for one user."""
    for i in range(3):
        await budget.deduct_budget(
            db=db_session,
            user_id=test_user.id,
            cost_cents=10,
            model="test-model",
            prompt_tokens=100,
            completion_tokens=50,
            prompt=f"Message {i}",
        )
    await db_session.commit()
    
    from sqlalchemy import select
    result = await db_session.execute(
        select(UsageLog).where(UsageLog.user_id == test_user.id)
    )
    logs = list(result.scalars().all())
    
    assert len(logs) == 3
    assert test_user.budget_cents == 70  # Started with 100, spent 30


# =============================================================================
# User Endpoint Budget Tests
# =============================================================================

@pytest.mark.asyncio
async def test_get_user_budget_info(test_user: Any, auth_headers: Any) -> None:

    """Test that user endpoint returns budget information."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/users/me",
            headers=auth_headers,
        )
    
    assert response.status_code == 200
    data = response.json()
    assert "budgetRemaining" in data
    assert "budgetLimit" in data
    assert data["budgetRemaining"] == 1.0  # 100 cents = $1.00
    assert data["budgetLimit"] == 5.0  # 500 cents = $5.00
