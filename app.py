# app.py
from datetime import datetime, timezone, timedelta
import joblib
import json
import numpy as np
import pandas as pd
import paho.mqtt.client as mqtt
import plotly.graph_objs as go
import queue
import streamlit as st
import time
import threading

# Optional: lightweight auto-refresh helper (install in requirements). If you don't want it, remove next import and the st_autorefresh call below.
try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except Exception:
    HAS_AUTOREFRESH = False

# ---------------------------
# Config (edit if needed)
# ---------------------------
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
TOPIC_SENSOR = "sic/dibimbing/492/alexander/day7/sensor"
TOPIC_OUTPUT = "sic/dibimbing/492/alexander/day7/output"
MODEL_PATH = "model/random_forest_model.pkl"   # put the .pkl in same repo

# timezone GMT+7 helper
TZ = timezone(timedelta(hours=7))


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------
# module-level queue used by MQTT thread (do NOT replace this with st.session_state inside callbacks)
# ---------------------------
GLOBAL_MQ = queue.Queue()

# ---------------------------
# Streamlit page setup
# ---------------------------

st.set_page_config(
    page_title="IoT ML Realtime Dashboard — Stable", layout="wide")
st.title("oT ML Realtime DLight Intensity Dashboard")
# --------------------------- done before starting worker)if "msg_queue" not in st.session_state:
# expose the global queue in session_state so UI can read it
st.session_state.msg_queue = GLOBAL_MQ

if "logs" not in st.session_state:
    st.session_state.logs = []         # list of dict rows

if "last" not in st.session_state:
    st.session_state.last = None

if "mqtt_thread_started" not in st.session_state:
    st.session_state.mqtt_thread_started = False

if "ml_model" not in st.session_state:
    st.session_state.ml_model = None

if "mqtt_pubc" not in st.session_state:
    st.session_state.mqtt_pubc = mqtt.Client()
    st.session_state.mqtt_pubc.connect(MQTT_BROKER, MQTT_PORT, 60)
    st.session_state.mqtt_pubc.loop_start()

# ---------------------------
# Load Model (safe)
# ---------------------------


@st.cache_resource
def load_ml_model(path):
    try:
        m = joblib.load(path)
        return m
    except Exception as e:
        # don't fail the app; just return None and show a warning in UI
        st.warning(f"Could not load ML model from {path}: {e}")
        return None


if st.session_state.ml_model is None:
    st.session_state.ml_model = load_ml_model(MODEL_PATH)
if st.session_state.ml_model:
    st.success(f"Model loaded: {MODEL_PATH}")
else:
    st.info(
        "No ML model loaded. Upload iot_temp_model.pkl in repo to enable predictions.")

# ---------------------------
# MQTT callbacks (use GLOBAL_MQ, NOT st.session_state inside callbacks)
# ---------------------------


def _on_connect(client, userdata, flags, rc):
    try:
        client.subscribe(TOPIC_SENSOR)
    except Exception:
        pass
    # push connection status into queue
    GLOBAL_MQ.put(
        {"_type": "status", "connected": (rc == 0), "ts": time.time()})


def _on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    try:
        data = json.loads(payload)
    except Exception:
        # push raw payload if JSON parse fails
        GLOBAL_MQ.put({"_type": "raw", "payload": payload, "ts": time.time()})
        return

    # push structured sensor message
    GLOBAL_MQ.put({"_type": "sensor", "data": data,
                  "ts": time.time(), "topic": msg.topic})

# ---------------------------
# Start MQTT thread (worker)
# ---------------------------


def start_mqtt_thread_once():
    def worker():
        client = mqtt.Client()
        client.on_connect = _on_connect
        client.on_message = _on_message
        # optional: configure username/password if needed:
        # client.username_pw_set(USER, PASS)
        while True:
            try:
                client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                client.loop_forever()
            except Exception as e:
                # push error into queue so UI can show it
                GLOBAL_MQ.put(
                    {"_type": "error", "msg": f"MQTT worker error: {e}", "ts": time.time()})
                time.sleep(5)  # backoff then retry

    if not st.session_state.mqtt_thread_started:
        t = threading.Thread(target=worker, daemon=True, name="mqtt_worker")
        t.start()
        st.session_state.mqtt_thread_started = True
        time.sleep(0.05)


# start thread
start_mqtt_thread_once()

# ---------------------------
# Helper Functions
# ---------------------------


def parse_time(timestr):
    try:
        t = datetime.strptime(timestr, "%H:%M:%S")
        return t.hour, t.minute, t.second
    except:
        return 0, 0, 0


label_mapping = {
    0: "Hemat",
    1: "Normal",
    2: "Boros"
}


def model_predict_label_and_conf(time_str, lumen):
    model = st.session_state.ml_model
    if model is None:
        return ("N/A", None)
    hour, minute, second = parse_time(time_str)
    X = [[float(lumen), hour, minute, second]]

    try:
        pred_encoded = model.predict(X)[0]
        label = label_mapping.get(pred_encoded, "ERR")
    except Exception:
        label = "ERR"
    prob = None
    if hasattr(model, "predict_proba"):
        try:
            prob = float(np.max(model.predict_proba(X)))
        except Exception:
            prob = None
    return (label, prob)


# ---------------------------
# Dummy Functions (for dummy values and dummy prediction)
# ---------------------------

def dummy_predict(time_str, lumen):
    if lumen is None:
        return "N/A", None

    if lumen >= 2000:
        conf = 0.80 + (1 - abs(lumen - 1000) / 1000) * 0.15
        return "Hemat", round(conf, 3)
    elif lumen <= 3000:        # Normal
        conf = 0.75 + (1 - abs(lumen - 2500) / 500) * 0.20
        return "Normal", round(conf, 3)

    elif lumen <= 4095:        # Boros
        conf = 0.80 + (1 - abs(lumen - 3500) / 600) * 0.18
        return "Boros", round(conf, 3)

    return "N/A", None


# ---------------------------
# Drain queue (process incoming msgs)
# ---------------------------
def process_queue():
    updated = False
    q = st.session_state.msg_queue
    while not q.empty():
        item = q.get()
        ttype = item.get("_type")
        if ttype == "status":
            # status - connection
            st.session_state.last_status = item.get("connected", False)
            updated = True
        elif ttype == "error":
            # show error
            st.error(item.get("msg"))
            updated = True
        elif ttype == "raw":
            row = {"ts": now_str(), "raw": item.get("payload")}
            st.session_state.logs.append(row)
            st.session_state.last = row
            updated = True
        elif ttype == "sensor":
            d = item.get("data", {})
            try:
                p_time = d.get("time")
            except Exception:
                p_time = None
            try:
                p_lumen = float(d.get("lumen"))
            except Exception:
                p_lumen = None

            hour, minute, second = parse_time(p_time)

            row = {
                "ts": datetime.fromtimestamp(item.get("ts", time.time()), TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "time": p_time,
                "lumen": p_lumen,
                "hour": hour,
                "minute": minute,
                "second": second
            }

            # ML prediction
            if p_lumen is not None and p_time is not None:
                label, conf = model_predict_label_and_conf(p_time, p_lumen)
            else:
                label, conf = ("N/A", None)

            row["pred"] = label
            row["conf"] = conf

            # simple anomaly: low confidence or z-score on latest window

            # z-score on temp using recent window
            anomaly = False
            lumens = [r["lumen"]
                      for r in st.session_state.logs if r.get("lumen") is not None]
            window = lumens[-30:] if len(lumens) > 0 else []
            if len(window) >= 5 and p_lumen is not None:
                mean = float(np.mean(window))
                std = float(np.std(window, ddof=0))
                if std > 0:
                    z = abs((p_lumen - mean) / std)
                    if z >= 3.0:
                        anomaly = True

            row["anomaly"] = anomaly
            st.session_state.last = row
            st.session_state.logs.append(row)
            # keep bounded
            if len(st.session_state.logs) > 5000:
                st.session_state.logs = st.session_state.logs[-5000:]
            updated = True

            # Auto-publish alert back to ESP32 (fire-and-forget client)
            pubc = st.session_state.mqtt_pubc
            try:
                if label == "Boros":
                    pubc.publish(TOPIC_OUTPUT, "BOROS", retain=True)
                elif label == "Hemat":
                    pubc.publish(TOPIC_OUTPUT, "HEMAT", retain=True)
                elif label == "Normal":
                    pubc.publish(TOPIC_OUTPUT, "NORMAL", retain=True)
                else:
                    pubc.publish(TOPIC_OUTPUT, "ANOMALY", retain=True)
            except Exception:
                pass
    return updated


# run once here to pick up immediately available messages
_ = process_queue()

# ---------------------------
# UI layout
# ---------------------------
# optionally auto refresh UI; requires streamlit-autorefresh in requirements
if HAS_AUTOREFRESH:
    st_autorefresh(interval=1000, limit=None, key="autorefresh")  # 1s refresh

left, right = st.columns([1, 2])

with left:
    st.header("Connection Status")
    st.write("Broker:", f"{MQTT_BROKER}:{MQTT_PORT}")
    connected = getattr(st.session_state, "last_status", None)
    st.metric("MQTT Connected", "Yes" if connected else "No")
    st.write("Topic:", TOPIC_SENSOR)
    st.markdown("---")

    st.header("Last Reading")
    if st.session_state.last:
        last = st.session_state.last
        st.write(f"P_Time: {last.get('time')}")
        st.write(f"Lumen: {last.get('lumen')}")
        st.write(f"Prediction: {last.get('pred')}")
        st.write(f"Confidence: {last.get('conf')}")
        st.write(f"Anomaly flag: {last.get('anomaly')}")
    else:
        st.info("Waiting for data...")

    st.markdown("---")
    st.header("Manual Output Control")
    col1, col2, col3 = st.columns(3)
    pubc = st.session_state.mqtt_pubc
    if col1.button("Send BOROS"):
        try:
            pubc.publish(TOPIC_OUTPUT, "BOROS", retain=True)
            st.success("Published BOROS")
        except Exception as e:
            st.error(f"Publish failed: {e}")
    if col2.button("Send NORMAL"):
        try:
            pubc.publish(TOPIC_OUTPUT, "NORMAL", retain=True)
            st.success("Published NORMAL")
        except Exception as e:
            st.error(f"Publish failed: {e}")
    if col3.button("Send HEMAT"):
        try:
            pubc.publish(TOPIC_OUTPUT, "HEMAT", retain=True)
            st.success("Published HEMAT")
        except Exception as e:
            st.error(f"Publish failed: {e}")

    st.markdown("---")
    st.header("Download Logs")
    if st.button("Download CSV"):
        if st.session_state.logs:
            df_dl = pd.DataFrame(st.session_state.logs)
            csv = df_dl.to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV file", data=csv,
                               file_name=f"iot_logs_{int(time.time())}.csv")
        else:
            st.info("No logs to download")

with right:
    st.header("Live Chart (last 200 points)")
    df_plot = pd.DataFrame(st.session_state.logs[-200:])
    if (not df_plot.empty) and {"time", "lumen"}.issubset(df_plot.columns):
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_plot["time"], y=df_plot["lumen"], mode="lines+markers", name="Lumen (lm)"))
        fig.update_layout(
            yaxis=dict(title="Lumen (lm)"),
            height=520
        )
        # color markers by anomaly / label
        colors = []
        for _, r in df_plot.iterrows():
            if r.get("anomaly"):
                colors.append("magenta")
            else:
                lab = r.get("pred", "")
                if lab == "Boros":
                    colors.append("red")
                elif lab == "Normal":
                    colors.append("green")
                elif lab == "Hemat":
                    colors.append("blue")
                else:
                    colors.append("gray")
        fig.update_traces(marker=dict(size=8, color=colors),
                          selector=dict(mode="lines+markers"))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data yet. Make sure ESP32 publishes to correct topic.")

    st.markdown("### Recent Logs")
    if st.session_state.logs:
        st.dataframe(pd.DataFrame(st.session_state.logs)[::-1].head(100))
    else:
        st.write("—")


# after UI render, drain queue (so next rerun shows fresh data)
process_queue()
process_queue()
