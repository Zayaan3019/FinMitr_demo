from fastapi.testclient import TestClient
from app.timeseries.api import router
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_preprocess_endpoint():
    response = client.post(
        "/timeseries/preprocess", json={"data": [1, None, 2, None, 3], "method": "ffill"}
    )
    assert response.status_code == 200
    assert response.json() == [1, 1, 2, 2, 3]
