import asyncio
from datetime import datetime, timedelta, timezone
import random
from app.db.session import AsyncSessionLocal
from app.db.models.telemetry import Telemetry
from sqlalchemy import select
from app.db.models.device import Device

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Device).where(Device.hardware_uid.like("STM32_MOCK_%")).limit(1))
        device = result.scalar_one_or_none()
        if not device:
            print("No device found")
            return
        
        now = datetime.now(timezone.utc)
        records = []
        for i in range(24):
            dt = now - timedelta(hours=24 - i)
            records.append(
                Telemetry(
                    device_id=device.id,
                    time=dt,
                    temperature_c=random.uniform(50.0, 65.0),
                    humidity_pct=random.uniform(40.0, 60.0),
                    co2_ppm=random.uniform(400, 800),
                    ph_level=random.uniform(6.5, 7.5),
                )
            )
        session.add_all(records)
        await session.commit()
        print("Mock data generated for device", device.id)

asyncio.run(main())
