#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "DDSWrapper.h"
#include "Robot_controlcmd.hpp"
#include "Robot_setmode.hpp"
#include "Robot_status.hpp"

namespace {

using Clock = std::chrono::system_clock;

constexpr const char* kRobotStatusTopic = "Robot_Status_Topic";
constexpr const char* kControlCmdTopic = "Control_Cmd_Topic";
constexpr const char* kControlModeTopic = "Control_Mode_Topic";

std::atomic<bool> g_running{true};
std::atomic<std::uint64_t> g_status_samples{0};

std::string json_escape(const std::string& s) {
  std::string out;
  out.reserve(s.size());
  for (char c : s) {
    switch (c) {
      case '"':
      case '\\':
        out.push_back('\\');
        out.push_back(c);
        break;
      case '\n':
        out += "\\n";
        break;
      case '\r':
        out += "\\r";
        break;
      case '\t':
        out += "\\t";
        break;
      default:
        out.push_back(c);
        break;
    }
  }
  return out;
}

std::int64_t now_us() {
  const auto now = Clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::microseconds>(now).count();
}

struct CachedState {
  bool has_status = false;
  std::uint16_t workmode = 0;
  std::array<double, 2> joy_axes{0.0, 0.0};
  std::array<double, 4> imu_ori{0.0, 0.0, 0.0, 0.0};
  std::array<double, 3> imu_angular_vel{0.0, 0.0, 0.0};
  std::array<double, 3> imu_linear_acc{0.0, 0.0, 0.0};
  std::int64_t motor_timestamp = 0;
  std::int64_t update_timestamp = 0;
  std::string motors_json = "[]";
};

std::mutex g_state_mutex;
CachedState g_state;

void emit_ready() {
  std::cout << "{\"type\":\"ready\"}" << std::endl;
}

void emit_error(const std::string& message) {
  std::cout << "{\"type\":\"error\",\"message\":\"" << json_escape(message) << "\"}" << std::endl;
}

void emit_state_locked() {
  std::cout
      << "{\"type\":\"state\",\"state\":{"
      << "\"transport_ready\":true,"
      << "\"status_received\":" << (g_state.has_status ? "true" : "false") << ","
      << "\"workmode\":" << g_state.workmode << ","
      << "\"joy_axes\":[" << g_state.joy_axes[0] << "," << g_state.joy_axes[1] << "],"
      << "\"imu\":{"
      << "\"ori\":[" << g_state.imu_ori[0] << "," << g_state.imu_ori[1] << "," << g_state.imu_ori[2] << "," << g_state.imu_ori[3] << "],"
      << "\"angular_vel\":[" << g_state.imu_angular_vel[0] << "," << g_state.imu_angular_vel[1] << "," << g_state.imu_angular_vel[2] << "],"
      << "\"linear_acc\":[" << g_state.imu_linear_acc[0] << "," << g_state.imu_linear_acc[1] << "," << g_state.imu_linear_acc[2] << "]"
      << "},"
      << "\"motors\":" << g_state.motors_json << ","
      << "\"motor_timestamp\":" << g_state.motor_timestamp << ","
      << "\"timestamp_us\":" << g_state.update_timestamp
      << "}}" << std::endl;
}

void emit_state() {
  std::lock_guard<std::mutex> lock(g_state_mutex);
  emit_state_locked();
}

void log_debug(const std::string& message) {
  std::cerr << "[e1_dds_bridge] " << message << std::endl;
}

std::string motors_to_json(const std::vector<RobotStatus::MotorState>& motors) {
  std::ostringstream out;
  out << "[";
  for (std::size_t i = 0; i < motors.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    const auto& m = motors[i];
    out << "{"
        << "\"motor_id\":" << m.motor_id() << ","
        << "\"pos\":" << m.pos() << ","
        << "\"vel\":" << m.vel() << ","
        << "\"tau\":" << m.tau() << ","
        << "\"error\":" << m.error() << ","
        << "\"temperature\":" << m.temperature()
        << "}";
  }
  out << "]";
  return out.str();
}

bool extract_number(const std::string& line, const std::string& key, double& out) {
  const auto pos = line.find("\"" + key + "\"");
  if (pos == std::string::npos) {
    return false;
  }
  const auto colon = line.find(':', pos);
  if (colon == std::string::npos) {
    return false;
  }
  out = std::strtod(line.c_str() + colon + 1, nullptr);
  return true;
}

bool extract_string(const std::string& line, const std::string& key, std::string& out) {
  const auto pos = line.find("\"" + key + "\"");
  if (pos == std::string::npos) {
    return false;
  }
  const auto colon = line.find(':', pos);
  const auto q1 = line.find('"', colon + 1);
  const auto q2 = line.find('"', q1 + 1);
  if (colon == std::string::npos || q1 == std::string::npos || q2 == std::string::npos) {
    return false;
  }
  out = line.substr(q1 + 1, q2 - q1 - 1);
  return true;
}

int action_from_name(const std::string& action_name) {
  static const std::unordered_map<std::string, int> kActionMap = {
      {"WALK", 0},
      {"SWING", 1},
      {"SHAKE", 2},
      {"CHEER", 3},
      {"RUN", 4},
      {"START", 5},
      {"SWITCH", 6},
      {"STARTTEACH", 7},
      {"SAVETEACH", 8},
      {"ENDTEACH", 9},
      {"PLAYTEACH", 10},
      {"DEFAULT", 11},
  };
  const auto it = kActionMap.find(action_name);
  return it == kActionMap.end() ? 11 : it->second;
}

std::string action_for_gesture(const std::string& gesture_name) {
  if (gesture_name == "wave") {
    return "SWING";
  }
  if (gesture_name == "dance") {
    return "SHAKE";
  }
  if (gesture_name == "cheer" || gesture_name == "nod") {
    return "CHEER";
  }
  if (gesture_name == "run") {
    return "RUN";
  }
  return "DEFAULT";
}

class E1DDSBridge {
 public:
  explicit E1DDSBridge(int domain_id)
      : ddswrapper_(domain_id) {
    ddswrapper_.subscribeRobotStatus([](const RobotStatus::StatusData& data) {
      std::lock_guard<std::mutex> lock(g_state_mutex);
      g_state.has_status = true;
      g_state.workmode = data.workmode();
      g_state.joy_axes = data.joydata().axes();
      g_state.imu_ori = data.imudata().ori();
      g_state.imu_angular_vel = data.imudata().angular_vel();
      g_state.imu_linear_acc = data.imudata().linear_acc();
      g_state.motor_timestamp = data.motorstatearray().timestamp();
      g_state.update_timestamp = now_us();
      g_state.motors_json = motors_to_json(data.motorstatearray().motorstates());
      const auto sample_count = ++g_status_samples;
      if (sample_count == 1 || sample_count % 20 == 0) {
        std::ostringstream oss;
        oss << "status_sample count=" << sample_count
            << " workmode=" << g_state.workmode
            << " motors=" << data.motorstatearray().motorstates().size()
            << " joy0=" << g_state.joy_axes[0]
            << " joy1=" << g_state.joy_axes[1];
        log_debug(oss.str());
      }
    });
  }

  void publish_control(double vx, double vyaw, const std::string& action_name, std::uint16_t data) {
    RobotControlCmd::ControlCmd cmd;
    cmd.axes()[0] = vyaw;
    cmd.axes()[1] = vx;
    cmd.action() = action_from_name(action_name);
    cmd.data() = data;
    std::ostringstream oss;
    oss << "publish_control vx=" << vx
        << " vyaw=" << vyaw
        << " action=" << action_name
        << "(" << cmd.action() << ")"
        << " data=" << data;
    log_debug(oss.str());
    ddswrapper_.publishControlCmdData(cmd);
  }

  void publish_mode(std::uint16_t mode) {
    RobotSetMode::SetMode msg;
    msg.mode(mode);
    log_debug("publish_mode mode=" + std::to_string(mode));
    ddswrapper_.publishModeData(msg);
  }

 private:
  legged::DDSWrapper ddswrapper_;
};

void handle_line(E1DDSBridge& bridge, const std::string& line) {
  log_debug("stdin " + line);
  if (line.find("\"type\":\"cmd_vel\"") != std::string::npos) {
    double vx = 0.0;
    double vyaw = 0.0;
    std::string action_name = "DEFAULT";
    extract_number(line, "vx", vx);
    extract_number(line, "vyaw", vyaw);
    extract_string(line, "action", action_name);
    bridge.publish_control(vx, vyaw, action_name, 0);
    return;
  }

  if (line.find("\"type\":\"stop\"") != std::string::npos) {
    bridge.publish_control(0.0, 0.0, "DEFAULT", 0);
    return;
  }

  if (line.find("\"type\":\"set_mode_name\"") != std::string::npos) {
    std::string mode = "walking";
    extract_string(line, "mode", mode);
    const std::uint16_t value = (mode == "walking" || mode == "active") ? 1 : 0;
    bridge.publish_mode(value);
    return;
  }

  if (line.find("\"type\":\"gesture\"") != std::string::npos) {
    std::string name = "DEFAULT";
    extract_string(line, "name", name);
    bridge.publish_control(0.0, 0.0, action_for_gesture(name), 0);
    return;
  }

  if (line.find("\"type\":\"shutdown\"") != std::string::npos) {
    log_debug("shutdown requested");
    g_running = false;
    return;
  }

  emit_error("unsupported command");
}

}  // namespace

int main(int argc, char** argv) {
  int domain_id = 0;
  std::string dds_config_path;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--domain" && i + 1 < argc) {
      domain_id = std::atoi(argv[++i]);
    } else if (arg == "--dds-config" && i + 1 < argc) {
      dds_config_path = argv[++i];
    }
  }

  if (!dds_config_path.empty()) {
#if defined(_WIN32)
    _putenv_s("CYCLONEDDS_URI", dds_config_path.c_str());
#else
    std::string uri = dds_config_path;
    if (uri.rfind("file://", 0) != 0) {
      uri = "file://" + uri;
    }
    setenv("CYCLONEDDS_URI", uri.c_str(), 1);
#endif
  }

  std::signal(SIGINT, [](int) { g_running = false; });
  std::signal(SIGTERM, [](int) { g_running = false; });

  try {
    E1DDSBridge bridge(domain_id);
    emit_ready();

    std::thread poller([]() {
      while (g_running.load()) {
        emit_state();
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
      }
    });

    std::string line;
    while (g_running.load() && std::getline(std::cin, line)) {
      if (line.empty()) {
        continue;
      }
      try {
        handle_line(bridge, line);
      } catch (const std::exception& exc) {
        emit_error(exc.what());
      }
    }

    g_running = false;
    poller.join();
  } catch (const std::exception& exc) {
    emit_error(exc.what());
    return 1;
  }

  return 0;
}
