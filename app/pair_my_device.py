import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models.user import User
from app.db.models.device import Device
from app.db.models.user_device import UserDevice
import uuid

async def main():
    async with AsyncSessionLocal() as session:
        # Find user
        result = await session.execute(select(User).where(User.email == 'maosama0001@gmail.com'))
        user = result.scalar_one_or_none()
        if not user:
            print("User not found!")
            return
            
        # Create device
        dev_id = uuid.uuid4()
        device = Device(
            id=dev_id,
            hardware_uid=f"STM32_MOCK_{str(dev_id)[:8]}",
            device_secret_enc="mock",
            is_paired=True,
            display_name="My Smart Composter"
        )
        session.add(device)
        await session.flush()
        
        # Link user to device
        link = UserDevice(
            user_id=user.id,
            device_id=dev_id
        )
        session.add(link)
        await session.commit()
        print(f"Device {dev_id} paired successfully to {user.email}")

asyncio.run(main())
