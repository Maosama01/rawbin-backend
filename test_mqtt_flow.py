import asyncio
import json
import time
import httpx
import paho.mqtt.client as mqtt

API_URL = "http://localhost:8000/api/v1"

async def main():
    print("1. Registering test user...")
    async with httpx.AsyncClient() as client:
        # Register
        email = f"test_{int(time.time())}@rawbin.io"
        password = "SecurePassword123!"
        resp = await client.post(f"{API_URL}/auth/register", json={
            "email": email,
            "password": password,
            "full_name": "Test User",
            "display_name": "TestUser"
        })
        if resp.status_code not in (200, 201):
            print("Failed to register:", resp.text)
            return
        
        # Login
        resp = await client.post(f"{API_URL}/auth/login", json={
            "email": email,
            "password": password
        })
        if resp.status_code != 200:
            print("Failed to login:", resp.text)
            return
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("   -> Logged in successfully.")

        # 2. Register Device
        print("2. Registering device...")
        resp = await client.post(f"{API_URL}/devices/pair/challenge", headers=headers, json={
            "hardware_uid": f"STM32_{int(time.time())}",
            "display_name": "Test Bin"
        })
        if resp.status_code not in (200, 201):
            print("Failed to register device:", resp.text)
            return
            
        device = resp.json()
        device_id = device["device_id"]
        print(f"   -> Device created: {device_id}")
        
        # 2.5 Force Pair the device in the database so telemetry API doesn't reject it
        print("2.5 Force pairing device in DB...")
        from sqlalchemy import update
        from app.db.session import AsyncSessionLocal
        from app.db.models.device import Device
        async with AsyncSessionLocal() as db:
            await db.execute(update(Device).where(Device.id == device_id).values(is_paired=True))
            await db.commit()
        
        # Write device_id to a file for the C++ script
        with open("last_device_id.txt", "w") as f:
            f.write(device_id)

        # 3. Publish MQTT Telemetry
        print("3. Publishing MQTT Telemetry via Mosquitto...")
        topic = f"rawbin/telemetry/{device_id}"
        payload = json.dumps({
            "temperature_c": 55.4,
            "humidity_pct": 65.0,
            "co2_ppm": 1200.5,
            "ph_level": 6.8
        })
        
        mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        mqtt_client.connect("mosquitto", 1883, 60)
        mqtt_client.publish(topic, payload)
        mqtt_client.disconnect()
        print(f"   -> Published to {topic}")

        # 4. Wait a moment for background task to ingest into TimescaleDB
        print("4. Waiting 2 seconds for ingestion...")
        await asyncio.sleep(2)

        # 5. Verify via REST API
        print("5. Fetching telemetry history via API...")
        resp = await client.get(f"{API_URL}/telemetry/{device_id}/history?interval=raw", headers=headers)
        data = resp.json()
        print("   -> API Response:", data)
        if data["count"] > 0:
            print("\n✅ SUCCESS: End-to-end MQTT to Database pipeline is fully working!")
        else:
            print("\n❌ FAILURE: Telemetry not found in DB.")

if __name__ == "__main__":
    asyncio.run(main())
