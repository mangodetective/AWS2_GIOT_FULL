// src/s3/s3.controller.ts
// AWS S3에서 JSON 파일을 안전하게 가져오는 컨트롤러
//
// 🎯 주요 기능:
// 1. 파일명 자동 파싱: 202508081711_raw.json → rawdata/2025/08/08/17/
// 2. 스마트 검색: 파일명만으로 S3에서 자동으로 파일 찾기
// 3. JSON 구조 지원: 직접 배열 형태
// 4. 마지막 데이터 추출: 전체 배열에서 마지막 요소만 반환
// 5. 날짜별 일괄 조회: 특정 날짜의 모든 JSON 파일 데이터 반환
// 6. 폴더 타입별 우선 검색: rawdata, houravg, minavg 폴더 지원
// 7. 캐시 최적화: HTTP 캐시 헤더로 성능 향상
// 8. 강력한 에러 처리: AWS 에러를 사용자 친화적인 HTTP 에러로 변환
//
// 🔗 API 엔드포인트:
// - GET /s3/file/:filename           → 파일명으로 json의 전체 데이터
// - GET /s3/file/last/:filename      → 파일명으로 json의 마지막 데이터만
// - GET /s3/date/:date               → 날짜(YYYYMMDD)별 모든 JSON 파일 데이터

// NestJS에서 필요한 데코레이터와 예외 클래스들을 임포트
import {
  BadRequestException, // 400 에러 (잘못된 요청)
  Controller,          // 컨트롤러 데코레이터  
  Get,                // GET 요청 데코레이터
  NotFoundException,   // 404 에러 (파일 없음)
  Param,              // URL 파라미터 추출
  Res,                // 응답 객체 접근
} from '@nestjs/common';
import type { Response } from 'express'; // Express 응답 타입
import { S3Service } from './s3.service'; // S3 연결 서비스

// '/s3' 경로로 들어오는 모든 요청을 처리하는 컨트롤러
@Controller('s3')
export class S3Controller {
  // S3Service를 주입받아서 사용 (의존성 주입)
  constructor(private readonly s3Service: S3Service) {}

  /**
   * 🎯 파일명으로 전체 데이터를 가져오는 핵심 API
   *
   * 사용 사례:
   * - 센서 데이터 파일의 모든 측정값이 필요한 경우
   * - 데이터 분석용 원본 데이터 다운로드
   * - 특정 시간대 전체 데이터 조회
   *
   * 자동화 기능:
   * 1. 파일명 패턴 자동 인식 (YYYYMMDDHHMM_raw.json)
   * 2. S3 경로 자동 생성 (rawdata/YYYY/MM/DD/HH/)
   * 3. 파일 존재 확인 및 다운로드
   *
   * @route GET /s3/file/:filename
   * @param filename 파일명 (예: "202508081711_raw.json")
   * @param res
   * @returns 파일의 전체 JSON 데이터
   *
   * @example
   * GET /s3/file/202508081711_raw.json
   *
   * 응답:
   * [
   *   {"timestamp":"2025-08-08T17:11:00","temp":27.1,"hum":61.5,"gas":676},
   *   {"timestamp":"2025-08-08T17:11:05","temp":27.1,"hum":61.5,"gas":764},
   *   ...
   * ]
   */
  @Get('file/:filename')
  async getDataByFilename(
    @Param('filename') filename: string,      // URL 파라미터에서 파일명 추출
    @Res({ passthrough: true }) res: Response, // HTTP 응답 객체 (캐시 헤더 설정용)
  ) {
    // 파일명에서 폴더타입 자동 감지
    const folderType = this.detectFolderType(filename);
    const key = await this.mapFileIdToPath(filename, folderType);
    const data = await this.fetch(key);
    
    res.setHeader('Cache-Control', 'public, max-age=60');
    
    return data; // 전체 JSON 데이터 반환
  }

  /**
   * 🎯 파일명으로 검색해서 JSON의 마지막 데이터만 반환하는 API
   * 
   * 사용 사례:
   * - 실시간 모니터링: 최신 센서 값만 필요한 경우
   * - 대시보드 표시: 현재 상태 확인
   * - API 응답 최적화: 전체 데이터 대신 최신값만 전송
   * 
   * 🔄 지원하는 JSON 구조:
   * 1. 직접 배열: [sensor_data1, sensor_data2, ...]
   * 
   * 💡 자동 구조 인식:
   * - 파일의 JSON 구조를 자동으로 분석
   * - 직접 배열에서 마지막 요소 추출
   * 
   * @route GET /s3/file/last/:filename
   * @param filename 파일명 (예: "202508081711_raw.json")
   * @returns 마지막 데이터 + 메타데이터
   * 
   * @example
   * GET /s3/file/last/202508081711_raw.json
   * 
   * 응답 (직접 배열 구조):
   * {
   *   "totalRecords": 12,
   *   "data": {"timestamp":"2025-08-08T17:11:55","temp":27.1,"hum":61.7,"gas":800},
   *   "lastDataOnly": true,
   *   "dataType": "direct_array"
   * }
   * 
   */
  @Get('file/last/:filename')
  async getLastDataFromFile(
    @Param('filename') filename: string,        // URL 파라미터에서 파일명 추출
    @Res({ passthrough: true }) res: Response,  // HTTP 응답 객체
  ) {
    // 파일명에서 폴더타입 자동 감지
    const folderType = this.detectFolderType(filename);
    const key = await this.mapFileIdToPath(filename, folderType);
    const data = await this.fetch(key);
    
    // 2. JSON 구조 확인 및 마지막 데이터 추출
    
    // === 직접 배열 형태 (rawdata 파일) ===
    // 예: [{"timestamp": "...", "temp": 27.1, ...}, {"timestamp": "...", "temp": 27.2, ...}]
    if (Array.isArray(data) && data.length > 0) {
      const lastIndex = data.length - 1;
      const lastData = data[lastIndex];
      
      res.setHeader('Cache-Control', 'public, max-age=60');
      return {
        data: lastData,              // 마지막 데이터
      };
    }
    
    // === 객체 형태 (houravg, minavg 파일) ===
    // 예: {"averages": {...}, "trends": {...}, ...}
    if (typeof data === 'object' && data !== null && !Array.isArray(data)) {
      res.setHeader('Cache-Control', 'public, max-age=60');
      return {
        data: data,                  // 전체 객체 데이터
      };
    }
    
    // === 에러: 지원하지 않는 데이터 구조 ===
    throw new BadRequestException(
      'Unsupported data format. Expected array or object.',
    );
  }

  /**
   * 🗓️ 날짜별 모든 JSON 파일 조회 API
   * 
   * 특정 날짜(YYYYMMDD)에 저장된 모든 JSON 파일의 내용을 가져와 반환합니다.
   * 
   * 사용 사례:
   * - 특정 날짜의 모든 센서 데이터 조회
   * - 일일 데이터 분석 및 백업
   * - 시간대별 데이터 패턴 분석
   * 
   * 🔍 동작 방식:
   * 1. 날짜 형식 검증 (YYYYMMDD)
   * 2. S3 경로 생성: rawdata/YYYY/MM/DD/HH/
   * 3. 해당 폴더의 모든 JSON 파일 목록 조회
   * 4. 각 파일의 내용을 병렬로 다운로드
   * 5. 파일명별로 정리하여 반환
   * 
   * @route GET /s3/date/:date
   * @param date 날짜 문자열 (YYYYMMDD 형식, 예: "20250808")
   * @param res HTTP 응답 객체 (캐시 헤더 설정용)
   * @returns 해당 날짜의 모든 JSON 파일 데이터
   * 
   * @example
   * GET /s3/date/20250808
   * 
   * 응답:
   * {
   *   "date": "20250808",
   *   "totalFiles": 24,
   *   "files": {
   *     "202508080000_raw.json": [{"timestamp": "...", "data": {...}}],
   *     "202508080100_raw.json": [{"timestamp": "...", "data": {...}}],
   *     "202508080200_raw.json": [{"timestamp": "...", "data": {...}}],
   *     ...
   *     "202508082300_raw.json": [{"timestamp": "...", "data": {...}}]
   *   }
   * }
   */
  @Get('date/:date')
  async getDataByDate(
    @Param('date') date: string,                    // URL 파라미터에서 날짜 추출
    @Res({ passthrough: true }) res: Response,      // HTTP 응답 객체 (캐시 헤더 설정용)
  ) {
    // 날짜 형식 검증
    if (!/^\d{8}$/.test(date)) {
      throw new BadRequestException('Invalid date format. Use YYYYMMDD (e.g., 20250808)');
    }

    try {
      // 1. S3에서 해당 날짜의 모든 JSON 파일 목록 조회
      const fileKeys = await this.s3Service.getFilesByDate(date, 'rawdata');
      
      if (fileKeys.length === 0) {
        throw new NotFoundException(`No JSON files found for date: ${date}`);
      }

      // 2. 모든 파일의 내용을 병렬로 가져오기
      const filesData: Record<string, any> = {};
      
      await Promise.all(
        fileKeys.map(async (key) => {
          try {
            // 파일명만 추출 (경로에서 마지막 부분)
            const filename = key.split('/').pop() || key;
            // 파일 내용 가져오기
            const data = await this.s3Service.getJson(key);
            filesData[filename] = data;
          } catch (error) {
            console.error(`Error reading file ${key}:`, error);
            // 개별 파일 에러는 무시하고 계속 진행
            const filename = key.split('/').pop() || key;
            filesData[filename] = { error: 'Failed to read file' };
          }
        })
      );

      // 3. 캐시 헤더 설정 (하루 단위 데이터는 오래 캐시 가능)
      res.setHeader('Cache-Control', 'public, max-age=3600'); // 1시간 캐시

      return {
        date,
        totalFiles: fileKeys.length,
        files: filesData,
      };
    } catch (error) {
      // S3 에러를 적절한 HTTP 에러로 변환
      if (error instanceof BadRequestException || error instanceof NotFoundException) {
        throw error;
      }
      
      console.error(`Error getting data for date ${date}:`, error);
      throw new BadRequestException(`Failed to retrieve data for date: ${date}`);
    }
  }


  // ---- 헬퍼 메서드들 ----

  /**
   * 🔍 파일명에서 폴더타입을 자동으로 감지하는 메서드
   * 
   * 파일명 패턴에 따라 적절한 S3 폴더를 결정합니다:
   * - _raw.json, _rawdata.json → rawdata 폴더
   * - _houravg.json → houravg 폴더  
   * - _minavg.json → minavg 폴더
   * - 기타 → rawdata 폴더 (기본값)
   * 
   * @param filename 파일명
   * @returns 폴더타입 ('rawdata' | 'houravg' | 'minavg')
   */
  private detectFolderType(filename: string): 'rawdata' | 'houravg' | 'minavg' {
    if (filename.includes('_houravg.json')) {
      console.log(`📁 폴더타입 감지: ${filename} → houravg`);
      return 'houravg';
    }
    
    if (filename.includes('_minavg.json')) {
      console.log(`📁 폴더타입 감지: ${filename} → minavg`);
      return 'minavg';
    }
    
    // raw, rawdata 또는 기타 모든 경우
    console.log(`📁 폴더타입 감지: ${filename} → rawdata (기본값)`);
    return 'rawdata';
  }

  /**
   * 🔧 파일 ID를 실제 S3 경로로 지능적으로 변환하는 핵심 헬퍼 메서드
   * 
   * 이 메서드는 모든 파일 검색 API의 핵심 로직입니다:
   * 
   * 🎯 주요 기능:
   * 1. 스마트 파싱: 파일명 패턴 자동 인식 및 S3 경로 생성
   * 2. 보안 검증: 허용된 문자만 사용하여 경로 조작 공격 방지
   * 3. 다중 검색: 여러 가능한 경로를 시도하여 파일 발견율 최대화
   * 4. 로깅 최소화: 에러 상황만 로깅
   * 
   * 🔍 검색 전략 (우선순위 순):
   * 1. 날짜 패턴 파싱: YYYYMMDDHHMM_raw.json → [folderType]/YYYY/MM/DD/HH/
   * 2. 지정된 폴더 우선 검색: [folderType]/파일명, [folderType]/파일명.json
   * 3. 다른 폴더들 검색: rawdata/, houravg/, minavg/
   * 4. 루트 경로: 파일명.json
   * 
   * @param fileId 파일 식별자 (파일명 또는 ID)
   * @param folderType 우선 검색할 폴더 ('rawdata' | 'houravg' | 'minavg'), 기본값: 'rawdata'
   * @returns Promise<string> S3 키 (전체 경로)
   * @throws BadRequestException 잘못된 형식의 fileId
   * @throws NotFoundException 모든 경로에서 파일을 찾지 못한 경우
   * 
   * @example
   * // 날짜 패턴 파일명 (rawdata 폴더 우선)
   * await mapFileIdToPath("202508081711_raw.json", "rawdata")
   * // 결과: "rawdata/2025/08/08/17/202508081711_raw.json"
   * 
   * @example  
   * // 일반 파일명 (houravg 폴더 우선)
   * await mapFileIdToPath("sensor_config", "houravg")
   * // 결과: "houravg/sensor_config.json" (첫 번째로 발견된 경로)
   */
  private async mapFileIdToPath(fileId: string, folderType: 'rawdata' | 'houravg' | 'minavg' = 'rawdata'): Promise<string> {
    console.log(`🔍 파일 검색 시작: ${fileId}, 폴더타입: ${folderType}`);
    
    if (!fileId) {
      throw new BadRequestException('fileId is required');
    }
    
    // 보안 검증: Path Traversal 공격 방지
    if (!/^[a-zA-Z0-9_.-]+$/.test(fileId)) {
      console.log(`❌ 보안 검증 실패: ${fileId}`);
      throw new BadRequestException('Invalid file ID format - only alphanumeric, dash, underscore, and dot allowed');
    }

    // 스마트 파싱: 날짜 패턴 인식 (raw, rawdata, houravg, minavg 지원)
    const dateMatch = fileId.match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})_(raw|rawdata|houravg|minavg)\.json$/);
    
    if (dateMatch) {
      const [, year, month, day, hour] = dateMatch;
      const smartPath = `${folderType}/${year}/${month}/${day}/${hour}/${fileId}`;
      console.log(`📅 날짜 패턴 매치: ${fileId} → ${smartPath}`);
      
      const exists = await this.s3Service.checkFileExists(smartPath);
      if (exists) {
        console.log(`✅ 스마트 검색 성공: ${smartPath}`);
        return smartPath;
      } else {
        console.log(`❌ 스마트 경로에서 파일 없음: ${smartPath}`);
      }
    } else {
      console.log(`📋 날짜 패턴 매치 실패: ${fileId}`);
    }

    // Fallback: 다중 경로 검색 (지정된 폴더 우선)
    const allFolders = ['rawdata', 'houravg', 'minavg'];
    const otherFolders = allFolders.filter(folder => folder !== folderType);
    
    const candidatePaths = [
      // 지정된 폴더 우선 검색
      `${folderType}/${fileId}`,
      `${folderType}/${fileId}.json`,
      // 다른 폴더들 검색
      ...otherFolders.flatMap(folder => [
        `${folder}/${fileId}`,
        `${folder}/${fileId}.json`,
      ]),
      // 루트 경로 마지막 시도
      `${fileId}.json`,
    ];
    
    console.log(`🔄 Fallback 검색 시작: ${candidatePaths.length}개 후보 경로`);
    
    for (const path of candidatePaths) {
      console.log(`🔎 검색 중: ${path}`);
      const exists = await this.s3Service.checkFileExists(path);
      if (exists) {
        console.log(`✅ Fallback 검색 성공: ${path}`);
        return path;
      }
    }
    
    // 모든 시도 실패
    console.log(`❌ 파일 찾기 실패: ${fileId}, 검색한 경로들: ${candidatePaths.join(', ')}`);
    
    throw new NotFoundException(`File not found for ID: ${fileId}. Searched in: ${candidatePaths.join(', ')}`);
  }

  /**
   * S3에서 JSON 파일을 가져오고 에러 처리하는 메서드
   * AWS SDK의 에러를 사용자 친화적인 HTTP 에러로 변환
   */
  private async fetch(key: string) {
    try {
      // S3Service를 통해 JSON 파일 가져오기
      const data = await this.s3Service.getJson(key);
      
      // JSON이 올바른 객체 형태인지 확인
      if (typeof data !== 'object' || data === null) {
        throw new BadRequestException('S3 object is not valid JSON');
      }
      
      return data; // 성공시 데이터 반환
    } catch (err: any) {
      // AWS SDK 에러를 HTTP 상태코드로 변환
      const code = err?.$metadata?.httpStatusCode;
      
      // 404: 파일이 존재하지 않음
      if (code === 404 || err?.name === 'NoSuchKey') {
        throw new NotFoundException(`S3 object not found: ${key}`);
      }
      
      // 400: 환경변수 설정 문제
      if (err?.message?.includes('S3_BUCKET_NAME is not set')) {
        throw new BadRequestException(
          'Server misconfig: S3_BUCKET_NAME missing',
        );
      }
      
      // 기타 에러는 500 Internal Server Error로 처리
      throw err;
    }
  }
}