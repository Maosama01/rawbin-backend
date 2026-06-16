import re

with open("app/api/v1/devices.py", "r") as f:
    content = f.read()

demo_route = """

@router.post(
    "/demo",
    response_model=DeviceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a demo device with mock telemetry",
)
async def create_demo_device(
    current_user: CurrentUser,
    db: DbSession,
) -> Device:
    import random
    from app.db.models.telemetry import Telemetry
    
    dev_id = uuid.uuid4()
    device = Device(
        id=dev_id,
        hardware_uid=f"STM32_MOCK_{str(dev_id)[:8]}",
        device_secret_enc=encrypt_device_secret("mock"),
        display_name="Demo Smart Composter",
        is_paired=True,
    )
    db.add(device)
    await db.flush()
    await device_access.add_member(db, device.id, current_user.id)
    
    now = datetime.now(timezone.utc)
    records = []
    for i in range(24 * 4): # 15 min intervals
        dt = now - timedelta(hours=24) + timedelta(minutes=15 * i)
        records.append(
            Telemetry(
                device_id=device.id,
                time=dt,
                temperature_c=random.uniform(50.0, 65.0),
                humidity_pct=random.uniform(40.0, 60.0),
                co2_ppm=random.uniform(400.0, 800.0),
                ph_level=random.uniform(6.5, 7.5),
            )
        )
    db.add_all(records)
    logger.info("Created demo device", extra={"device_id": str(device.id)})
    return device

"""

# Insert before "def _challenge_key" or "Pairing"
content = content.replace("# ── Pairing", demo_route + "\n# ── Pairing")

with open("app/api/v1/devices.py", "w") as f:
    f.write(content)

