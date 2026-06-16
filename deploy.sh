#!/bin/bash
set -e

echo "=================================================="
echo " Rawbin Backend - VPS Automated Setup Script"
echo "=================================================="

# 1. Update system and install Git/Curl
echo "-> Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y git curl ufw

# 2. Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "-> Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    rm get-docker.sh
else
    echo "-> Docker is already installed."
fi

# 3. Configure Firewall (UFW)
echo "-> Configuring firewall..."
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 8000/tcp # FastAPI Backend
sudo ufw allow 1883/tcp # MQTT Broker
sudo ufw allow 5555/tcp # Celery Flower UI
echo "y" | sudo ufw enable

# 4. Clone or update repository
if [ -d "rawbin-backend" ]; then
    echo "-> Repository exists. Pulling latest changes..."
    cd rawbin-backend
    git pull
else
    echo "-> Cloning repository..."
    git clone https://github.com/Maosama01/rawbin-backend.git
    cd rawbin-backend
fi

# 5. Generate secure .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "-> Creating secure .env file for production..."
    SECRET_KEY=$(openssl rand -hex 32)
    DEVICE_SECRET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" || echo "bE0Wbq6XhI8-tF3dK4Qp5Yg1vA9z_D8xGk7Jm_ZfVwc=")
    
    cat <<EOF > .env
APP_ENV=production
DEBUG=False
SECRET_KEY=${SECRET_KEY}
DEVICE_SECRET_KEY=${DEVICE_SECRET_KEY}
ALLOWED_ORIGINS="*"

POSTGRES_USER=rawbin
POSTGRES_PASSWORD=$(openssl rand -hex 16)
POSTGRES_DB=rawbin
POSTGRES_HOST=db
POSTGRES_PORT=5432

REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=$(openssl rand -hex 16)

SMS_PROVIDER=stub

MQTT_BROKER_HOST=mosquitto
MQTT_BROKER_PORT=1883
MQTT_CLIENT_ID=rawbin-prod-listener
EOF
    echo "-> Generated fresh .env file with secure passwords."
fi

# 6. Build and start Docker containers
echo "-> Building and starting backend services..."
sudo docker compose up --build -d

# 7. Run database migrations
echo "-> Running database migrations..."
sleep 10 # Wait for DB to be healthy
sudo docker compose exec -T api alembic upgrade head

echo "=================================================="
echo " SETUP COMPLETE! "
echo " Your backend is now running 24/7."
echo "=================================================="
