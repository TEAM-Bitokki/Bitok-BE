"""저장한 식당 API"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.security import hash_token
from app.domains.auth.models import User
from app.domains.restaurant.models import Restaurant
from app.domains.saved.models import SavedRestaurant

V4 = "550e8400-e29b-41d4-a716-446655440000"
OTHER_V4 = "550e8400-e29b-41d4-a716-446655440001"
H = {"deviceToken": V4}
OTHER_H = {"deviceToken": OTHER_V4}


def _sign_up(client, token: str = V4) -> int:
    return client.post(
        "/api/auth/sign-up", json={"device_token": token}
    ).json()["user_id"]


async def _add_restaurant(session, place_id: str, name: str) -> Restaurant:
    restaurant = Restaurant(
        kakao_place_id=place_id,
        name=name,
        category="한식",
        address="서울시 용산구",
        lat=37.5,
        lng=127.0,
    )
    session.add(restaurant)
    await session.commit()
    return restaurant


def test_list_saved_restaurants_returns_empty_list(client):
    _sign_up(client)

    r = client.get("/api/saved-restaurants", headers=H)

    assert r.status_code == 200
    assert r.json() == {"items": []}


@pytest.mark.asyncio
async def test_save_and_list_restaurant(client, session):
    _sign_up(client)
    restaurant = await _add_restaurant(session, "place-1", "맛있는식당")

    saved = client.post(
        "/api/saved-restaurants",
        headers=H,
        json={"restaurant_id": restaurant.id},
    )
    listed = client.get("/api/saved-restaurants", headers=H)

    assert saved.status_code == 201
    assert listed.status_code == 200
    assert listed.json()["items"] == [
        {
            "restaurant_id": restaurant.id,
            "kakao_place_id": "place-1",
            "name": "맛있는식당",
            "category": "한식",
            "address": "서울시 용산구",
            "lat": 37.5,
            "lng": 127.0,
            "distance_m": None,
            "checked_menu_count": 0,
            "is_saved": True,
        }
    ]


@pytest.mark.asyncio
async def test_list_saved_restaurants_is_newest_first(client, session):
    user_id = _sign_up(client)
    older = await _add_restaurant(session, "place-old", "먼저 저장한 식당")
    newer = await _add_restaurant(session, "place-new", "나중에 저장한 식당")
    now = datetime.now(UTC)
    session.add_all(
        [
            SavedRestaurant(
                user_id=user_id,
                restaurant_id=older.id,
                saved_at=now - timedelta(days=1),
            ),
            SavedRestaurant(
                user_id=user_id,
                restaurant_id=newer.id,
                saved_at=now,
            ),
        ]
    )
    await session.commit()

    r = client.get("/api/saved-restaurants", headers=H)

    assert r.status_code == 200
    assert [item["restaurant_id"] for item in r.json()["items"]] == [
        newer.id,
        older.id,
    ]


@pytest.mark.asyncio
async def test_save_restaurant_is_idempotent(client, session):
    _sign_up(client)
    restaurant = await _add_restaurant(session, "place-1", "맛있는식당")
    body = {"restaurant_id": restaurant.id}

    first = client.post("/api/saved-restaurants", headers=H, json=body)
    second = client.post("/api/saved-restaurants", headers=H, json=body)
    count = await session.scalar(select(func.count()).select_from(SavedRestaurant))

    assert first.status_code == second.status_code == 201
    assert count == 1


def test_save_missing_restaurant_returns_404(client):
    _sign_up(client)

    r = client.post(
        "/api/saved-restaurants", headers=H, json={"restaurant_id": 999}
    )

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_unsave_restaurant(client, session):
    _sign_up(client)
    restaurant = await _add_restaurant(session, "place-1", "맛있는식당")
    client.post(
        "/api/saved-restaurants",
        headers=H,
        json={"restaurant_id": restaurant.id},
    )

    removed = client.delete(
        f"/api/saved-restaurants/{restaurant.id}", headers=H
    )
    removed_again = client.delete(
        f"/api/saved-restaurants/{restaurant.id}", headers=H
    )

    assert removed.status_code == 204
    assert removed_again.status_code == 404


@pytest.mark.asyncio
async def test_unsave_does_not_delete_another_users_item(client, session):
    _sign_up(client)
    _sign_up(client, OTHER_V4)
    restaurant = await _add_restaurant(session, "place-1", "맛있는식당")
    client.post(
        "/api/saved-restaurants",
        headers=OTHER_H,
        json={"restaurant_id": restaurant.id},
    )

    r = client.delete(f"/api/saved-restaurants/{restaurant.id}", headers=H)

    assert r.status_code == 404
    assert client.get("/api/saved-restaurants", headers=OTHER_H).json()["items"]
