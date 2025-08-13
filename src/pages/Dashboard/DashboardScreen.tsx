// Dashboard.tsx - 메인 대시보드 컴포넌트
import React, { useState, useEffect } from 'react';
import { LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { Bell, User, ChevronDown, Info } from 'lucide-react';
import {
  NotificationData,
  SensorData,
  SensorType,
  SidebarItemProps,
  DashboardAPI,
  DashboardUtils,
  SENSOR_OPTIONS,
  MENU_ITEMS
} from '../../services/DashboardTypes';
import styles from "./DashboardScreen.module.css";

interface DashboardScreenProps {
  onNavigateToChatbot: () => void;
  onNavigateToHistory: () => void;
  onNavigateToRole?: () => void;
}

// 사이드바 아이템 컴포넌트
const SidebarItem: React.FC<SidebarItemProps> = ({ icon, label, isActive, onClick }) => (
  <button
    onClick={onClick}
    className={`${styles.sidebarItem} ${isActive ? styles.sidebarItemActive : ''}`}
  >
    <span>{icon}</span>
    <span>{label}</span>
  </button>
);

// 알림 드롭다운 컴포넌트
const NotificationDropdown: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  notifications: NotificationData['notifications'];
}> = ({ isOpen, onClose, notifications }) => {
  if (!isOpen) return null;

  return (
    <>
      <div className={styles.dropdown}>
        <div className={styles.dropdownHeader}>
          <h3 className={styles.dropdownTitle}>알림</h3>
        </div>
        <div className={styles.notificationList}>
          {notifications.length === 0 ? (
            <div className={styles.emptyNotification}>
              새로운 알림이 없습니다
            </div>
          ) : (
            notifications.map((notification) => (
              <div
                key={notification.id}
                className={`${styles.notificationItem} ${!notification.read ? styles.notificationItemUnread : ''}`}
              >
                <p className={styles.notificationMessage}>{notification.message}</p>
                <p className={styles.notificationTimestamp}>{notification.timestamp}</p>
              </div>
            ))
          )}
        </div>
      </div>
      <button
        onClick={onClose}
        className={styles.dropdownOverlay}
        aria-label="알림 닫기"
      />
    </>
  );
};

// 관리자 드롭다운 컴포넌트
const AdminDropdown: React.FC<{ isOpen: boolean; onClose: () => void }> = ({ isOpen, onClose }) => {
  if (!isOpen) return null;

  return (
    <>
      <div className={styles.adminDropdown}>
        <div className={styles.adminDropdownContent}>
          <button className={styles.adminDropdownItem}>프로필 설정</button>
          <button className={styles.adminDropdownItem}>계정 관리</button>
          <div className={styles.adminDropdownDivider} />
          <button className={`${styles.adminDropdownItem} ${styles.adminDropdownLogout}`}>로그아웃</button>
        </div>
      </div>
      <button
        onClick={onClose}
        className={styles.dropdownOverlay}
        aria-label="관리자 메뉴 닫기"
      />
    </>
  );
};

// 센서 차트 컴포넌트
const SensorChart: React.FC<{
  sensorData: SensorData | null;
  isLoading: boolean;
  error: string | null;
}> = ({ sensorData, isLoading, error }) => {
  if (isLoading) {
    return (
      <div className={styles.loadingContainer}>
        <div>데이터를 불러오는 중...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={styles.errorContainer}>
        <div className={styles.errorTitle}>데이터 로딩 실패</div>
        <div className={styles.errorMessage}>{error}</div>
      </div>
    );
  }

  if (!sensorData || !sensorData.success) {
    return (
      <div className={styles.errorContainer}>
        <div className={styles.errorTitle}>데이터를 사용할 수 없습니다</div>
      </div>
    );
  }

  const chartData = sensorData.labels.map((label, index) => ({
    time: label,
    value: sensorData.values[index]
  }));

  const color = DashboardUtils.getChartColor(sensorData.sensorType);

  return (
    <div className={styles.chartContainer}>
      <ResponsiveContainer className={styles.responsiveContainer}>
        {sensorData.sensorType === 'gas' ? (
          <AreaChart data={chartData}>
            <CartesianGrid className={styles.chartGrid} />
            <XAxis
              dataKey="time"
              className={styles.chartAxis}
            />
            <YAxis
              className={styles.chartAxis}
            />
            <Tooltip
              wrapperClassName={styles.chartTooltip}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={color}
              fill={color}
              className={styles.chartArea}
            />
          </AreaChart>
        ) : (
          <LineChart data={chartData}>
            <CartesianGrid className={styles.chartGrid} />
            <XAxis
              dataKey="time"
              className={styles.chartAxis}
            />
            <YAxis
              className={styles.chartAxis}
            />
            <Tooltip
              wrapperClassName={styles.chartTooltip}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke={color}
              className={styles.chartLine}
              dot={{ fill: color, className: styles.chartDot }}
              activeDot={{ stroke: color, className: styles.chartActiveDot }}
            />
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
};

// 메인 대시보드 컴포넌트
const DashboardScreen: React.FC<DashboardScreenProps> = ({
  onNavigateToChatbot,
  onNavigateToHistory,
  onNavigateToRole,
}) => {
  const [activeMenu, setActiveMenu] = useState('Dashboard');
  const [notificationData, setNotificationData] = useState<NotificationData>({
    count: 0,
    notifications: []
  });
  const [isNotificationOpen, setIsNotificationOpen] = useState(false);
  const [isAdminMenuOpen, setIsAdminMenuOpen] = useState(false);
  const [selectedSensor, setSelectedSensor] = useState<SensorType>('temperature');
  const [sensorData, setSensorData] = useState<SensorData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [allSensorData, setAllSensorData] = useState<Record<SensorType, SensorData | null>>({
    temperature: null,
    humidity: null,
    gas: null,
  });

  // 센서 데이터 가져오기
  const fetchSensorData = async (sensorType: SensorType) => {
    setIsLoading(true);
    setError(null);

    try {
      const data = await DashboardAPI.getSensorData(sensorType);

      if (data.success) {
        setSensorData(data as SensorData);
        setAllSensorData(prev => ({
          ...prev,
          [sensorType]: data as SensorData
        }));
      } else {
        setError('데이터를 불러올 수 없습니다.');
      }
    } catch (err) {
      setError('데이터를 가져오는 중 오류가 발생했습니다.');
      console.error('센서 데이터 가져오기 실패:', err);
    } finally {
      setIsLoading(false);
    }
  };

  // ✅ REPLACE 전체 함수
  const fetchAllSensorData = async () => {
    try {
      const results = await Promise.all(
        SENSOR_OPTIONS.map(opt => DashboardAPI.getSensorData(opt.value as SensorType))
      );

      const newAllSensorData: Record<SensorType, SensorData | null> = {
        temperature: null,
        humidity: null,
        gas: null,
      };

      results.forEach((result, index) => {
        if (result.success) {
          const sensorType = SENSOR_OPTIONS[index].value as SensorType;
          newAllSensorData[sensorType] = result as SensorData;
        }
      });

      setAllSensorData(newAllSensorData);
    } catch (err) {
      console.error('전체 센서 데이터 가져오기 실패:', err);
    }
  };

  // 알림 데이터 가져오기
  const fetchNotifications = async () => {
    try {
      const data = await DashboardAPI.getNotifications();
      setNotificationData(data);
    } catch (error) {
      console.error('알림 데이터 가져오기 실패:', error);
    }
  };

  // 메뉴 클릭 핸들러
  const handleMenuClick = (label: string, path: string) => {
    setActiveMenu(label);

    switch (label) {
      case 'Chatbot':
        onNavigateToChatbot();
        break;
      case 'History':
        onNavigateToHistory();
        break;
      case 'Dashboard':
        // 대시보드면 현재 화면 유지
        break;
      case 'Logout':
        onNavigateToRole?.();  // 역할 선택 화면으로
        break;
      default:
        break;
    }
  };

  // 센서 선택 핸들러
  const handleSensorSelect = (sensorType: SensorType) => {
    setSelectedSensor(sensorType);
    fetchSensorData(sensorType);
  };

  // 컴포넌트 마운트 시 초기 데이터 로딩
  useEffect(() => {
    fetchNotifications();
    fetchSensorData('temperature'); // 기본값
    fetchAllSensorData(); // 테이블용 전체 데이터

    // 주기적으로 데이터 업데이트 (30초마다)
    const interval = setInterval(() => {
      fetchNotifications();
      fetchSensorData(selectedSensor);
      fetchAllSensorData();
    }, 30000);

    return () => clearInterval(interval);
  }, [selectedSensor]);

  // 선택된 센서 변경 시 데이터 다시 가져오기
  useEffect(() => {
    if (allSensorData[selectedSensor]) {
      setSensorData(allSensorData[selectedSensor]);
    }
  }, [selectedSensor, allSensorData]);

  // 실시간 시간 업데이트를 위한 useEffect 추가
  const [currentTime, setCurrentTime] = useState(DashboardUtils.getCurrentDateTime());

  useEffect(() => {
    const timeInterval = setInterval(() => {
      setCurrentTime(DashboardUtils.getCurrentDateTime());
    }, 60000); // 1분마다 업데이트

    return () => clearInterval(timeInterval);
  }, []);

  return (
    <div className={styles.dashboardContainer}>
      {/* 사이드바 */}
      <nav className={styles.sidebar}>
        <div className={styles.sidebarHeader}>
          <h2 className={styles.sidebarTitle}>AWS IOT</h2>
        </div>

        <div className={styles.sidebarMenu}>
          {MENU_ITEMS.map((item) => (
            <SidebarItem
              key={item.label}
              icon={item.icon}
              label={item.label}
              isActive={activeMenu === item.label}
              onClick={() => handleMenuClick(item.label, item.path)}
            />
          ))}
        </div>
      </nav>

      {/* 메인 컨텐츠 영역 */}
      <main className={styles.mainContent}>
        {/* 상단 헤더 */}
        <header className={styles.header}>
          <div className={styles.headerLeft}>
            <h1 className={styles.pageTitle}>{activeMenu}</h1>
            <p className={styles.pageSubtitle}>{currentTime}</p>
          </div>

          <div className={styles.headerRight}>
            {/* 알림 아이콘 */}
            <div className={styles.headerItem}>
              <button
                onClick={() => {
                  setIsNotificationOpen(!isNotificationOpen);
                  setIsAdminMenuOpen(false);
                }}
                className={styles.headerButton}
                aria-label="알림"
              >
                <Bell size={20} />
                {notificationData.count > 0 && (
                  <span className={styles.notificationBadge}>
                    {notificationData.count > 99 ? '99+' : notificationData.count}
                  </span>
                )}
              </button>

              <NotificationDropdown
                isOpen={isNotificationOpen}
                onClose={() => setIsNotificationOpen(false)}
                notifications={notificationData.notifications}
              />
            </div>

            {/* 관리자 메뉴 */}
            <div className={styles.headerItem}>
              <button
                onClick={() => {
                  setIsAdminMenuOpen(!isAdminMenuOpen);
                  setIsNotificationOpen(false);
                }}
                className={styles.adminButton}
                aria-label="관리자 메뉴"
              >
                <User size={20} />
                <span>관리자</span>
                <ChevronDown size={16} />
              </button>

              <AdminDropdown
                isOpen={isAdminMenuOpen}
                onClose={() => setIsAdminMenuOpen(false)}
              />
            </div>
          </div>
        </header>

        {/* 메인 대시보드 컨텐츠 */}
        <div className={styles.dashboardContent}>
          {activeMenu === 'Dashboard' ? (
            <>
              {/* 통합 데이터 테이블 섹션 */}
              <section className={styles.summarySection}>
                <div className={styles.sectionHeader}>
                  <div className={styles.sectionTitleRow}>
                    <h2 className={styles.sectionTitle}>CURRENT DATA & PREDICTION DATA<button
                      type="button"
                      className={styles.infoIcon}
                      aria-label="섹션 설명"
                      title="섹션 설명"
                    >
                      <Info size={16} />
                    </button></h2>
                  </div>
                </div>

                <div className={styles.summaryCard}>
                  <table className={styles.summaryTable}>
                    <thead>
                      <tr>
                        <th>TYPE</th>
                        <th>TIME</th>
                        <th>TEMPERATURE</th>
                        <th>HUMIDITY</th>
                        <th>GAS CONCENTRATION</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>CURRENT DATA</td>
                        <td>{currentTime}</td>
                        <td>
                          {allSensorData.temperature ? (
                            <span className={DashboardUtils.getStatusClass(allSensorData.temperature.current.status)}>
                              {allSensorData.temperature.current.value.toFixed(1)}{allSensorData.temperature.unit}
                            </span>
                          ) : (
                            <span>로딩 중...</span>
                          )}
                        </td>
                        <td>
                          {allSensorData.humidity ? (
                            <span className={DashboardUtils.getStatusClass(allSensorData.humidity.current.status)}>
                              {allSensorData.humidity.current.value.toFixed(1)}{allSensorData.humidity.unit}
                            </span>
                          ) : (
                            <span>로딩 중...</span>
                          )}
                        </td>
                        <td>
                          {allSensorData.gas ? (
                            <span className={DashboardUtils.getStatusClass(allSensorData.gas.current.status)}>
                              {allSensorData.gas.current.value.toFixed(0)}{allSensorData.gas.unit}
                            </span>
                          ) : (
                            <span>로딩 중...</span>
                          )}
                        </td>
                      </tr>
                      <tr>
                        <td>PREDICTION DATA</td>
                        <td>NEXT 1 HOUR (AVG)</td>
                        <td>
                          {allSensorData.temperature ? (
                            <span>{allSensorData.temperature.prediction.value.toFixed(1)}{allSensorData.temperature.unit}</span>
                          ) : (
                            <span>로딩 중...</span>
                          )}
                        </td>
                        <td>
                          {allSensorData.humidity ? (
                            <span>{allSensorData.humidity.prediction.value.toFixed(1)}{allSensorData.humidity.unit}</span>
                          ) : (
                            <span>로딩 중...</span>
                          )}
                        </td>
                        <td>
                          {allSensorData.gas ? (
                            <span>{allSensorData.gas.prediction.value.toFixed(0)}{allSensorData.gas.unit}</span>
                          ) : (
                            <span>로딩 중...</span>
                          )}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>

              {/* 시간평균 데이터 차트 섹션 */}
              <section className={styles.chartSection}>
                <div className={styles.sectionHeader}>
                  <h2 className={styles.sectionTitle}>TIME-AVERAGED DATA</h2>

                  {/* 센서 선택 드롭다운 */}
                  <div className={styles.sensorSelector}>
                    <select
                      value={selectedSensor}
                      onChange={(e) => handleSensorSelect(e.target.value as SensorType)}
                      className={styles.sensorSelect}
                    >
                      {SENSOR_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className={styles.chartCard}>
                  <SensorChart
                    sensorData={sensorData}
                    isLoading={isLoading}
                    error={error}
                  />
                </div>
              </section>
            </>
          ) : (
            // 다른 메뉴 선택 시 플레이스홀더
            <div className={styles.placeholderContent}>
              <h2 className={styles.placeholderTitle}>
                {activeMenu} 페이지
              </h2>
              <p className={styles.placeholderSubtitle}>
                현재 선택된 메뉴: {activeMenu}
              </p>
              <p className={styles.placeholderNote}>
                실제 페이지 컨텐츠를 여기에 구현하세요.
              </p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
};

export default DashboardScreen;
