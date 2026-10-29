// Thin acquisition-only adapters to vendor SDKs; no reverse-engineered packets.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <chrono>
#include <cmath>
#include <stdexcept>
#include <string>
namespace py = pybind11;

#ifdef WITH_UNITREE
#include "unitree_lidar_sdk.h"
class L2Reader {
    unilidar_sdk2::UnitreeLidarReader* reader_ = nullptr;
    bool open_ = false;
public:
    L2Reader(std::string lidar_ip, std::string local_ip, unsigned short lidar_port,
             unsigned short local_port, unsigned short scan_lines) {
        reader_ = unilidar_sdk2::createUnitreeLidarReader();
        if (!reader_ || reader_->initializeUDP(lidar_port,lidar_ip,local_port,local_ip,scan_lines,false,.1f,30.f))
            throw std::runtime_error("Unitree SDK UDP initialization failed");
        open_ = true;
        // Cloud and IMU must share the same device clock. No host-arrival stamping.
        reader_->syncLidarTimeStamp();
        reader_->startLidarRotation();
    }
    py::object poll() {
        if (!open_) throw std::runtime_error("L2 reader is closed");
        int kind;
        { py::gil_scoped_release release; kind=reader_->runParse(); }
        if (kind==LIDAR_IMU_DATA_PACKET_TYPE) {
            unilidar_sdk2::LidarImuData imu;
            if (!reader_->getImuData(imu)) return py::none();
            py::dict out;out["type"]="imu";
            out["timestamp"]=double(imu.info.stamp.sec)+1e-9*imu.info.stamp.nsec;
            py::array_t<float> g(3),a(3);
            for(int j=0;j<3;++j){g.mutable_at(j)=imu.angular_velocity[j];a.mutable_at(j)=imu.linear_acceleration[j];}
            out["gyro"]=g;out["accel"]=a;return out;
        }
        if (kind==LIDAR_POINT_DATA_PACKET_TYPE) {
            unilidar_sdk2::PointCloudUnitree cloud;
            if (!reader_->getPointCloud(cloud)) return py::none();
            py::array_t<float> points({py::ssize_t(cloud.points.size()),py::ssize_t(6)});
            auto p=points.mutable_unchecked<2>();float duration=0;
            for(py::ssize_t i=0;i<py::ssize_t(cloud.points.size());++i){
                const auto& v=cloud.points[i];p(i,0)=v.x;p(i,1)=v.y;p(i,2)=v.z;
                p(i,3)=v.intensity;p(i,4)=v.time;p(i,5)=float(v.ring);duration=std::max(duration,v.time);
            }
            py::dict out;out["type"]="cloud";out["timestamp"]=cloud.stamp;
            out["frame_id"]=cloud.id;out["duration"]=duration;out["points"]=points;return out;
        }
        return py::none();
    }
    void close(){if(open_){reader_->stopLidarRotation();reader_->closeUDP();open_=false;}}
    ~L2Reader(){close();}
    // The vendor factory exposes no destruction function and its base class has
    // no virtual destructor. Its single reader allocation is process-lifetime;
    // do not delete an unknown derived object through that base pointer.
};
#endif

#ifdef WITH_SLAMTEC
#include "sl_lidar.h"
#include "sl_lidar_driver.h"
class S2Reader {
    sl::ILidarDriver* driver_=nullptr;
    sl::IChannel* channel_=nullptr;
    bool serial_=true;
public:
    S2Reader(std::string port,unsigned baudrate,std::string transport,unsigned udp_port){
        if(transport!="serial"&&transport!="udp")throw std::invalid_argument("S2 transport must be serial or udp");
        if(transport=="udp"&&(udp_port==0||udp_port>65535))throw std::invalid_argument("Invalid S2 UDP port");
        serial_=transport=="serial";
        driver_=*sl::createLidarDriver();
        channel_=serial_?*sl::createSerialPortChannel(port,baudrate):*sl::createUdpChannel(port,udp_port);
        if(!driver_||!channel_||SL_IS_FAIL(driver_->connect(channel_))) {close();throw std::runtime_error("S2 SDK channel connection failed");}
        sl_lidar_response_device_health_t health;
        if(SL_IS_FAIL(driver_->getHealth(health))||health.status==SL_LIDAR_STATUS_ERROR){close();throw std::runtime_error("S2 health check failed");}
        if(serial_)driver_->setMotorSpeed();
        if(SL_IS_FAIL(driver_->startScan(false,true))){close();throw std::runtime_error("S2 scan start failed");}
    }
    py::array_t<float> read(){
        if(!driver_)throw std::runtime_error("S2 reader closed");
        sl_lidar_response_measurement_node_hq_t nodes[8192];size_t count=8192;sl_result result;
        {py::gil_scoped_release release;result=driver_->grabScanDataHq(nodes,count,2000);}
        if(SL_IS_FAIL(result))throw std::runtime_error("S2 acquisition timeout/error");
        driver_->ascendScanData(nodes,count);
        py::array_t<float> values({py::ssize_t(count),py::ssize_t(3)});auto v=values.mutable_unchecked<2>();
        for(size_t i=0;i<count;++i){v(i,0)=nodes[i].dist_mm_q2*.00025f;v(i,1)=nodes[i].angle_z_q14*(3.14159265358979323846f/32768.f);v(i,2)=float(nodes[i].quality);}
        return values;
    }
    void close(){if(driver_){driver_->stop();if(serial_)driver_->setMotorSpeed(0);driver_->disconnect();delete driver_;driver_=nullptr;}if(channel_){delete channel_;channel_=nullptr;}}
    ~S2Reader(){close();}
};
#endif

PYBIND11_MODULE(lab_sensors_native,m){
    m.doc()="Official Unitree L2 and Slamtec S2 acquisition adapters";
#ifdef WITH_UNITREE
    py::class_<L2Reader>(m,"L2Reader").def(py::init<std::string,std::string,unsigned short,unsigned short,unsigned short>())
        .def("poll",&L2Reader::poll).def("close",&L2Reader::close);
#endif
#ifdef WITH_SLAMTEC
    py::class_<S2Reader>(m,"S2Reader").def(py::init<std::string,unsigned,std::string,unsigned>(),
        py::arg("port"),py::arg("baudrate"),py::arg("transport")="serial",py::arg("udp_port")=8089)
        .def("read",&S2Reader::read).def("close",&S2Reader::close);
#endif
}
