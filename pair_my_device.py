import asyncio
from sqlalchemy import select, update
from app.core.database import async_session_maker
from app.models.user import User
from app.models.device import Device
import uuid

async def main():
    async with async_session_maker() as session:
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
            owner_id=user.id,
            hardware_uid="STM32_MOCK_123",
            device_secret_enc="mock",
            is_paired=True,
            display_name="My Smart Composter"
        )
        session.add(device)
        await session.commit()
        print(f"Device {dev_id} paired successfully to {user.email}")

asyncio.run(main())
