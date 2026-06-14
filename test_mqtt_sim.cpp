#include <iostream>
#include <string>
#include <thread>
#include <chrono>
#include "mqtt/async_client.h"

// The Mosquitto broker is running locally on port 1883
const std::string SERVER_ADDRESS = "tcp://localhost:1883";
const std::string CLIENT_ID = "mac_cpp_simulator";

// We will use the device_id you got from the Swagger UI earlier!
// Replace this UUID with your actual Device ID from Step 3.
const std::string DEVICE_ID = "YOUR-DEVICE-ID-HERE"; 
const std::string TOPIC = "rawbin/telemetry/" + DEVICE_ID;

int main(int argc, char* argv[]) {
    std::cout << "Starting C++ MQTT Simulator..." << std::endl;

    // Create an async MQTT client
    mqtt::async_client client(SERVER_ADDRESS, CLIENT_ID);

    mqtt::connect_options connOpts;
    connOpts.set_keep_alive_interval(20);
    connOpts.set_clean_session(true);

    try {
        std::cout << "Connecting to local Mosquitto broker at " << SERVER_ADDRESS << "..." << std::flush;
        client.connect(connOpts)->wait();
        std::cout << "OK!" << std::endl;

        // Simulate sending 5 fake temperature readings
        for (int i = 1; i <= 5; ++i) {
            // Create a fake JSON payload
            float fake_temp = 50.0f + (i * 2.5f); // 52.5, 55.0, 57.5...
            std::string payload = 
                "{"
                "\"temperature_c\": " + std::to_string(fake_temp) + ", "
                "\"humidity_pct\": 60.5, "
                "\"co2_ppm\": 1100"
                "}";

            std::cout << "Publishing reading " << i << "/5 to topic: " << TOPIC << std::endl;
            std::cout << "  Payload: " << payload << std::endl;

            // Publish to broker
            mqtt::message_ptr pubmsg = mqtt::make_message(TOPIC, payload);
            pubmsg->set_qos(1); // Quality of Service 1 (guaranteed delivery)
            client.publish(pubmsg)->wait();

            // Wait 2 seconds before sending the next one
            std::this_thread::sleep_for(std::chrono::seconds(2));
        }

        std::cout << "Disconnecting..." << std::flush;
        client.disconnect()->wait();
        std::cout << "OK!" << std::endl;
    }
    catch (const mqtt::exception& exc) {
        std::cerr << "\nMQTT Error: " << exc.what() << std::endl;
        return 1;
    }

    std::cout << "Simulation complete! Check Swagger UI (GET /telemetry) to see if it saved to the database!" << std::endl;
    return 0;
}
