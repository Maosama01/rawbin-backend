#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "MQTTClient.h"

#define ADDRESS     "tcp://localhost:1883"
#define CLIENTID    "mac_c_simulator"
#define DEVICE_ID   "26b47b56-fa18-498c-8650-131d881af934"
#define TOPIC       "rawbin/telemetry/26b47b56-fa18-498c-8650-131d881af934"
#define QOS         1
#define TIMEOUT     10000L

int main(int argc, char* argv[]) {
    MQTTClient client;
    MQTTClient_connectOptions conn_opts = MQTTClient_connectOptions_initializer;
    int rc;

    printf("Starting C MQTT Simulator...\n");

    if ((rc = MQTTClient_create(&client, ADDRESS, CLIENTID,
        MQTTCLIENT_PERSISTENCE_NONE, NULL)) != MQTTCLIENT_SUCCESS)
    {
         printf("Failed to create client, return code %d\n", rc);
         exit(EXIT_FAILURE);
    }

    conn_opts.keepAliveInterval = 20;
    conn_opts.cleansession = 1;

    printf("Connecting to local Mosquitto broker at %s...\n", ADDRESS);
    if ((rc = MQTTClient_connect(client, &conn_opts)) != MQTTCLIENT_SUCCESS)
    {
        printf("Failed to connect, return code %d\n", rc);
        exit(EXIT_FAILURE);
    }
    printf("OK! Connected.\n");

    // Simulate sending 5 fake temperature readings
    for (int i = 1; i <= 5; ++i) {
        float fake_temp = 50.0f + (i * 2.5f); // 52.5, 55.0, 57.5...
        char payload[256];
        snprintf(payload, sizeof(payload), 
            "{\"temperature_c\": %.1f, \"humidity_pct\": 60.5, \"co2_ppm\": 1100}", 
            fake_temp);

        printf("Publishing reading %d/5 to topic: %s\n", i, TOPIC);
        printf("  Payload: %s\n", payload);

        MQTTClient_message pubmsg = MQTTClient_message_initializer;
        pubmsg.payload = payload;
        pubmsg.payloadlen = (int)strlen(payload);
        pubmsg.qos = QOS;
        pubmsg.retained = 0;

        MQTTClient_deliveryToken token;
        if ((rc = MQTTClient_publishMessage(client, TOPIC, &pubmsg, &token)) != MQTTCLIENT_SUCCESS) {
             printf("Failed to publish message, return code %d\n", rc);
             exit(EXIT_FAILURE);
        }

        rc = MQTTClient_waitForCompletion(client, token, TIMEOUT);
        printf("  Message delivered (token: %d)\n", token);

        // Wait 2 seconds before sending the next one
        sleep(2);
    }

    printf("Disconnecting...\n");
    if ((rc = MQTTClient_disconnect(client, 10000)) != MQTTCLIENT_SUCCESS) {
        printf("Failed to disconnect, return code %d\n", rc);
    }
    
    MQTTClient_destroy(&client);
    printf("Simulation complete! Check Swagger UI (GET /telemetry) to see if it saved to the database!\n");
    return rc;
}
