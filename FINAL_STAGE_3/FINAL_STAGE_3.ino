#include <WiFi.h>
#include <PubSubClient.h>
#include "time.h"

#define LDRPIN 35
#define RED_PIN 27
#define YELLOW_PIN 26
#define GREEN_PIN 25

const char* ssid = "Zen";
const char* password = "l1m4nt099";

const char* mqtt_server = "broker.hivemq.com";
const int mqtt_port = 1883;
const char* mqtt_topic_temp = "sic/dibimbing/492/alexander/day7/sensor"; 
const char* mqtt_topic_sub = "sic/dibimbing/492/alexander/day7/output";
String clientId = "ESP32Client-" + String((uint32_t)ESP.getEfuseMac(), HEX);

WiFiClient espClient;
PubSubClient client(espClient);

unsigned long lastLDRRead = 0;
const unsigned long interval = 1000;

const char* ntpServer = "pool.ntp.org";
const long gmtOffset_sec = 7 * 3600; // GMT+7
const int daylightOffset_sec = 0;

void setup_wifi(){
  delay(10);
  Serial.print("Connecting to: ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);

  while(WiFi.status() != WL_CONNECTED){
    delay(500);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("Wifi connected");
  Serial.print("IP Address: ");
  Serial.println(WiFi.localIP());
}

void reconnect(){
  while(!client.connected()){
    Serial.print("Reconnecting to MQTT Broker...");
    if(client.connect(clientId.c_str())){
      Serial.println("Connected");
      client.subscribe(mqtt_topic_sub);
    }else{
      Serial.print("Failed, rc=");
      Serial.print(client.state());
      Serial.println(" Try again in 5 seconds");
      delay(5000);
    }
  }
}

String getCurrentTime(){
  struct tm timeinfo;
  if(!getLocalTime(&timeinfo)){
    return "00:00:00";
  }
  char timeStr[9];
  strftime(timeStr, sizeof(timeStr), "%H:%M:%S", &timeinfo);
  return String(timeStr);
}


void readLDR(){
int light = analogRead(LDRPIN);
  Serial.print("Light Intensity: ");
  Serial.println(light);

  String now = getCurrentTime();

  // Upload to MQTT dengan waktu
  String msg = "{\"time\": \"" + now + "\", \"lumen\": " + String(light) + "}";
  client.publish(mqtt_topic_temp, msg.c_str());
}

void callback(char* topic, byte* payload, unsigned int length){
  String msg;
  for (int i = 0; i < length; i++) {
    msg += (char)payload[i];
  }
  msg.trim();
  Serial.print("Output from ML: ");
  Serial.println(msg);

  if(msg == "HEMAT"){
    digitalWrite(RED_PIN, LOW);
    digitalWrite(YELLOW_PIN, LOW);
    digitalWrite(GREEN_PIN, HIGH);
  }else if(msg == "NORMAL"){
    digitalWrite(RED_PIN, LOW);
    digitalWrite(YELLOW_PIN, HIGH);
    digitalWrite(GREEN_PIN, LOW);
  }else if(msg == "BOROS"){
    digitalWrite(RED_PIN, HIGH);
    digitalWrite(YELLOW_PIN, LOW);
    digitalWrite(GREEN_PIN, LOW);
  }
}

void setup() {
  // put your setup code here, to run once:
  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
  Serial.begin(115200);
  
  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
  reconnect();
  client.subscribe(mqtt_topic_sub);

  pinMode(34, INPUT);
  pinMode(RED_PIN, OUTPUT);
  pinMode(YELLOW_PIN, OUTPUT);
  pinMode(GREEN_PIN, OUTPUT);
  Serial.println("Begin");
}

void loop() {
  // put your main code here, to run repeatedly:
  if(!client.connected()) reconnect();
  client.loop();
  if(millis() - lastLDRRead >= interval){
    lastLDRRead = millis();
    readLDR();
  }
}
