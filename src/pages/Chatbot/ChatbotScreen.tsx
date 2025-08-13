// ChatbotScreen.tsx - 챗봇 화면 컴포넌트
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { 
  Bell, 
  User, 
  ChevronDown,
  ChevronLeft, 
  Printer, 
  Star, 
  Trash2, 
  Paperclip, 
  Send,
  MoreHorizontal,
  MessageCircle
} from 'lucide-react';
import { ChatbotState, ChatMessage, ChatbotAPI, ChatbotUtils } from '../../services/ChatbotTypes';
import { 
  NotificationData, 
  DashboardAPI, 
  SidebarItemProps,
  MENU_ITEMS,
  DashboardUtils
} from '../../services/DashboardTypes';
import styles from "./ChatbotScreen.module.css"

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

// 타이핑 인디케이터 컴포넌트
const TypingIndicator: React.FC = () => (
  <div className={`${styles.chatbotMessageItem} ${styles.bot}`}>
    <div className={`${styles.chatbotMessageAvatar} ${styles.bot}`}>AWS²</div>
    <div className={styles.chatbotTypingIndicator}>
      <div className={styles.chatbotTypingBubble}>
        <div className={styles.chatbotTypingDot}></div>
        <div className={styles.chatbotTypingDot}></div>
        <div className={styles.chatbotTypingDot}></div>
      </div>
    </div>
  </div>
);

// 메시지 아이템 컴포넌트
const MessageItem: React.FC<{ message: ChatMessage }> = ({ message }) => {
  const isBot = message.sender === 'bot';
  
  return (
    <div className={`${styles.chatbotMessageItem} ${styles[message.sender]}`}>
      {isBot && (
        <div className={`${styles.chatbotMessageAvatar} ${styles.bot}`}>AWS²</div>
      )}
      
      <div className={styles.chatbotMessageBubble}>
        <div className={`${styles.chatbotMessageContent} ${styles[message.sender]}`}>
          {message.message.split('\n').map((line, index) => (
            <React.Fragment key={index}>
              {line}
              {index < message.message.split('\n').length - 1 && <br />}
            </React.Fragment>
          ))}
          
          {/* 센서 데이터 표시 (봇 메시지인 경우) */}
          {isBot && message.sensorData && (
            <div className={styles.chatbotSensorData}>
              <div className={styles.chatbotSensorItem}>
                <span>온도:</span>
                <span>{message.sensorData.temperature}°C</span>
              </div>
              <div className={styles.chatbotSensorItem}>
                <span>습도:</span>
                <span>{message.sensorData.humidity}%</span>
              </div>
              <div className={styles.chatbotSensorItem}>
                <span>가스농도:</span>
                <span>{message.sensorData.gasConcentration}ppm</span>
              </div>
            </div>
          )}
        </div>
        
        <div className={`${styles.chatbotMessageMeta} ${styles[message.sender]}`}>
          <span className={styles.chatbotMessageTime}>
            {ChatbotUtils.formatTime(message.timestamp)}
          </span>
          <div className={styles.chatbotMessageActions}>
            <button className={styles.chatbotMessageActionButton}>
              <MoreHorizontal size={12} />
            </button>
          </div>
        </div>
      </div>
      
      {!isBot && (
        <div className={`${styles.chatbotMessageAvatar} ${styles.user}`}>A</div>
      )}
    </div>
  );
};

// 알림 드롭다운 컴포넌트 (Dashboard에서 재사용)
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
            <div className={styles.emptyNotification}>새로운 알림이 없습니다</div>
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

// 관리자 드롭다운 컴포넌트 (Dashboard에서 재사용)
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

interface ChatbotScreenProps {
  onNavigateToDashboard: () => void;
  onNavigateToHistory: () => void;
  onNavigateToRole?: () => void;
}

const ChatbotScreen: React.FC<ChatbotScreenProps> = ({ 
  onNavigateToDashboard,
  onNavigateToHistory,
  onNavigateToRole,
}) => {
  const [activeMenu, setActiveMenu] = useState('Chatbot');
  const [chatbotState, setChatbotState] = useState<ChatbotState>({
    messages: [],
    isLoading: false,
    isTyping: false,
    inputMessage: '',
    error: null,
    modelStatus: 'Active'
  });

  const [notificationData, setNotificationData] = useState<NotificationData>({
    count: 0,
    notifications: []
  });
  
  const [isNotificationOpen, setIsNotificationOpen] = useState(false);
  const [isAdminMenuOpen, setIsAdminMenuOpen] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 메시지 영역 스크롤을 맨 아래로
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  // 메시지 전송
  const sendMessage = useCallback(async () => {
    const message = chatbotState.inputMessage.trim();
    
    // 메시지 검증
    const validation = ChatbotUtils.validateMessage(message);
    if (!validation.isValid) {
      setChatbotState(prev => ({ ...prev, error: validation.error || null }));
      return;
    }

    // 사용자 메시지 추가
    const userMessage: ChatMessage = {
      id: ChatbotUtils.generateMessageId(),
      message,
      sender: 'user',
      timestamp: new Date().toISOString()
    };

    setChatbotState(prev => ({
      ...prev,
      messages: [...prev.messages, userMessage],
      inputMessage: '',
      isLoading: true,
      isTyping: true,
      error: null
    }));

    try {
      // API 호출 (개발 시에는 목 응답 사용)
      const response = await ChatbotAPI.generateMockResponse(message);
      
      if ('success' in response && response.success) {
        // 타이핑 효과를 위한 지연
        const typingDelay = ChatbotUtils.calculateTypingDelay(response.reply);
        
        setTimeout(() => {
          const botMessage: ChatMessage = {
            id: ChatbotUtils.generateMessageId(),
            message: response.reply,
            sender: 'bot',
            timestamp: response.timestamp,
            sensorData: response.sensorData,
            status: response.status
          };

          setChatbotState(prev => ({
            ...prev,
            messages: [...prev.messages, botMessage],
            isLoading: false,
            isTyping: false
          }));
        }, typingDelay);
      } else {
  const msg = 'error' in response ? response.error : 'Unknown error';
  throw new Error(msg);
}
    } catch (error) {
      setChatbotState(prev => ({
        ...prev,
        isLoading: false,
        isTyping: false,
        error: error instanceof Error ? error.message : '답변을 생성할 수 없습니다.'
      }));
    }
  }, [chatbotState.inputMessage]);

  // 입력 필드 변경 핸들러
  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    if (value.length <= 300) {
      setChatbotState(prev => ({ 
        ...prev, 
        inputMessage: value,
        error: null 
      }));
    }
  }, []);

  // 키보드 이벤트 핸들러
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!chatbotState.isLoading && chatbotState.inputMessage.trim()) {
        sendMessage();
      }
    }
  }, [chatbotState.isLoading, chatbotState.inputMessage, sendMessage]);

  // 메뉴 클릭 핸들러
  const handleMenuClick = (label: string, path: string) => {
    setActiveMenu(label);

    switch (label) {
      case 'Dashboard':
        onNavigateToDashboard();
        break;
      case 'History':
        onNavigateToHistory();
        break;
      case 'Chatbot':
        // 챗봇 화면이므로 현재 화면 유지
        break;
      case 'Logout':
        onNavigateToRole?.();  // 역할 선택 화면으로
        break;
      default:
        break;
    }
  };

  // 알림 데이터 가져오기
  const fetchNotifications = useCallback(async () => {
    try {
      const data = await DashboardAPI.getNotifications();
      setNotificationData(data);
    } catch (error) {
      console.error('알림 데이터 가져오기 실패:', error);
    }
  }, []);

  // 컴포넌트 마운트 시 초기화
  useEffect(() => {
    // 환영 메시지 추가
    const welcomeMessage = ChatbotUtils.createWelcomeMessage();
    setChatbotState(prev => ({
      ...prev,
      messages: [welcomeMessage]
    }));

    // 알림 데이터 가져오기
    fetchNotifications();

    // 입력 필드에 포커스
    setTimeout(() => {
      inputRef.current?.focus();
    }, 100);
  }, [fetchNotifications]);

  // 메시지 추가 시 스크롤
  useEffect(() => {
    scrollToBottom();
  }, [chatbotState.messages, chatbotState.isTyping, scrollToBottom]);

  // 메시지 히스토리 저장
  useEffect(() => {
    if (chatbotState.messages.length > 0) {
      ChatbotUtils.saveMessageHistory(chatbotState.messages);
    }
  }, [chatbotState.messages]);

  // 실시간 시간 업데이트를 위한 useEffect 추가
  const [currentTime, setCurrentTime] = useState(DashboardUtils.getCurrentDateTime());

  useEffect(() => {
    const timeInterval = setInterval(() => {
      setCurrentTime(DashboardUtils.getCurrentDateTime());
    }, 60000); // 1분마다 업데이트

    return () => clearInterval(timeInterval);
  }, []);

  const canSend = chatbotState.inputMessage.trim().length > 0 && !chatbotState.isLoading;
  const charCount = chatbotState.inputMessage.length;
  const charCountClass = charCount > 250 ? 'error' : charCount > 200 ? 'warning' : '';

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
          {/* 챗봇 메인 영역 */}
          <div className={styles.chatbotContainer}>
          {/* 챗봇 헤더 */}
          <div className={styles.chatbotHeader}>
            <div className={styles.chatbotHeaderLeft}>
              <button 
                className={styles.chatbotBackButton}
                onClick={onNavigateToDashboard}
                aria-label="뒤로 가기"
              >
                <ChevronLeft size={20} />
              </button>
              
              <div className={styles.chatbotModelInfo}>
                <div className={styles.chatbotModelName}>Model Name</div>
                <div className={styles.chatbotModelStatus}>
                  <span className={`${styles.chatbotStatusBadge} ${styles[`chatbotStatus${chatbotState.modelStatus}`]}`}>
                    {chatbotState.modelStatus}
                  </span>
                </div>
              </div>
            </div>
            
            <div className={styles.chatbotHeaderActions}>
              <button className={styles.chatbotActionButton} aria-label="인쇄">
                <Printer size={18} />
              </button>
              <button className={styles.chatbotActionButton} aria-label="즐겨찾기">
                <Star size={18} />
              </button>
              <button className={styles.chatbotActionButton} aria-label="삭제">
                <Trash2 size={18} />
              </button>
            </div>
          </div>

          {/* 메시지 영역 */}
          <div className={styles.chatbotMessagesContainer}>
            <div className={styles.chatbotMessages}>
              {chatbotState.messages.map((message) => (
                <MessageItem key={message.id} message={message} />
              ))}
              
              {chatbotState.isTyping && <TypingIndicator />}
              
              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* 에러 메시지 */}
          {chatbotState.error && (
            <div className={styles.chatbotErrorMessage}>
              {chatbotState.error}
            </div>
          )}

          {/* 입력 영역 */}
          <div className={styles.chatbotInputContainer}>
            <div className={styles.chatbotInputWrapper}>
              <button 
                className={styles.chatbotAttachButton}
                aria-label="파일 첨부"
              >
                <Paperclip size={18} />
              </button>
              
              <div style={{ position: 'relative', flex: 1 }}>
                <textarea
                  ref={inputRef}
                  className={styles.chatbotInputField}
                  value={chatbotState.inputMessage}
                  onChange={handleInputChange}
                  onKeyDown={handleKeyDown}
                  placeholder="Write message"
                  rows={1}
                  disabled={chatbotState.isLoading}
                />
                <div className={`${styles.chatbotCharCounter} ${charCountClass}`}>
                  {charCount}/300
                </div>
              </div>
              
              <button 
                className={styles.chatbotSendButton}
                onClick={sendMessage}
                disabled={!canSend}
                aria-label="메시지 전송"
              >
                <Send size={18} />
              </button>
            </div>
          </div>
        </div>
        </div>
      </main>
    </div>
  );
};

export default ChatbotScreen;