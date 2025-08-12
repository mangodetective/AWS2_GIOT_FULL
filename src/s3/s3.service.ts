// src/s3/s3.service.ts
// AWS S3와 연결하여 파일을 읽고 존재 여부를 확인하는 서비스
// 
// 🎯 주요 기능:
// 1. S3 파일 읽기 및 JSON 파싱
// 2. 파일 존재 여부 확인 (HeadObject 사용)
// 3. 파일명에서 날짜 자동 파싱 (YYYYMMDDHHMM_raw.json 패턴)
// 4. 날짜별/날짜범위별 파일 검색
// 5. 파일명으로 S3에서 파일 찾기 (스마트 검색)
// 6. 폴더 타입별 파일 조회 (rawdata, houravg, minavg)
//
// 📁 S3 폴더 구조:
// - rawdata/YYYY/MM/DD/HH/파일명.json    → 원시 데이터 (Raw Data)
// - houravg/YYYY/MM/DD/HH/파일명.json    → 시간별 평균 데이터 
// - minavg/YYYY/MM/DD/HH/파일명.json     → 분별 평균 데이터

// NestJS의 의존성 주입을 위한 데코레이터
import { Injectable } from '@nestjs/common';
// AWS SDK에서 S3 관련 클래스들 임포트
import {
  S3Client,              // S3 클라이언트 (연결 및 인증)
  GetObjectCommand,      // 파일 내용 가져오기
  HeadObjectCommand,     // 파일 메타데이터만 가져오기 (존재 확인용)
  ListObjectsV2Command,  // 디렉토리/프리픽스별 파일 목록 조회
} from '@aws-sdk/client-s3';

// 다른 클래스에서 주입받아 사용할 수 있는 서비스로 등록
@Injectable()
export class S3Service {
  // S3 클라이언트와 버킷명을 private으로 저장
  private readonly s3: S3Client;
  private readonly bucket: string;

  /**
   * S3Service 생성자
   * 환경변수에서 설정을 읽어와 S3 클라이언트를 초기화
   */
  constructor() {
    // 환경변수에서 S3 버킷명 읽기
    this.bucket = process.env.S3_BUCKET_NAME || '';
    if (!this.bucket) throw new Error('S3_BUCKET_NAME is not set');

    // AWS S3 클라이언트 생성
    this.s3 = new S3Client({
      region: process.env.AWS_REGION || 'ap-northeast-2', // 기본값: 서울 리전
      // 📝 credentials를 명시하지 않으면 다음 순서로 자동 인증:
      // 1. 환경변수 (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
      // 2. EC2 IAM Role (프로덕션 환경 권장)
      // 3. ~/.aws/credentials 파일
    });
  }

  /**
   * S3에서 JSON 또는 NDJSON 파일을 읽어서 파싱된 객체로 반환
   * 
   * 지원하는 형식:
   * 1. 일반 JSON: [{"key": "value"}, {"key": "value"}]
   * 2. NDJSON (Newline Delimited JSON): 
   *    {"key": "value"}
   *    {"key": "value"}
   * 
   * @param key S3 객체 키 (파일 경로)
   * @returns 파싱된 JSON 배열
   */
  async getJson(key: string) {
    if (!key) throw new Error('key is required');

    console.log(`📥 S3에서 파일 읽기 시작: ${key}`);

    // S3에서 파일 내용 가져오기
    const res = await this.s3.send(
      new GetObjectCommand({ Bucket: this.bucket, Key: key }),
    );

    // 바이너리 데이터를 UTF-8 텍스트로 변환
    const text = await res.Body?.transformToString('utf-8');
    if (!text) throw new Error('Empty S3 object body');

    console.log(`📄 파일 내용 크기: ${text.length} characters`);

    try {
      // 먼저 일반 JSON으로 파싱 시도
      const parsed = JSON.parse(text);
      console.log(`✅ 일반 JSON으로 파싱 성공`);
      return parsed;
    } catch (jsonError) {
      console.log(`🔄 일반 JSON 파싱 실패, NDJSON으로 시도...`);
      
      try {
        // NDJSON 형식으로 파싱 (각 줄이 개별 JSON 객체)
        const lines = text.trim().split('\n');
        const jsonObjects = lines
          .filter(line => line.trim()) // 빈 줄 제거
          .map(line => JSON.parse(line.trim()));
        
        console.log(`✅ NDJSON으로 파싱 성공: ${jsonObjects.length}개 객체`);
        return jsonObjects;
      } catch (ndjsonError) {
        console.error(`❌ NDJSON 파싱도 실패:`, ndjsonError);
        throw new Error(`Failed to parse as JSON or NDJSON: ${jsonError.message}`);
      }
    }
  }

  /**
   * S3에서 파일이 존재하는지 확인 (파일 내용은 다운로드하지 않음)
   * @param key S3 객체 키 (파일 경로)
   * @returns 파일 존재 여부 (true/false)
   */
  async checkFileExists(key: string): Promise<boolean> {
    try {
      // HeadObject는 메타데이터만 가져오므로 빠르고 비용 효율적
      await this.s3.send(
        new HeadObjectCommand({ Bucket: this.bucket, Key: key }),
      );
      return true; // 성공하면 파일 존재
    } catch {
      return false; // 에러 발생하면 파일 없음
    }
  }

  /**
   * 파일명에서 날짜 정보를 파싱하여 S3 경로를 자동 생성
   * 
   * 이 기능의 핵심:
   * - 파일명에서 년도, 월, 일, 시간을 자동으로 추출
   * - S3의 디렉토리 구조에 맞춰 경로 생성
   * - 복잡한 S3 경로를 기억할 필요 없이 파일명만으로 접근 가능
   * 
   * @param filename 파일명 (반드시 YYYYMMDDHHMM_raw.json 패턴이어야 함)
   *                 예: "202508081711_raw.json"
   *                     → 2025년 08월 08일 17시 11분 raw 데이터
   * @returns S3 경로 (예: "rawdata/2025/08/08/17/202508081711_raw.json")
   *          패턴이 맞지 않으면 null 반환
   * 
   * @example
   * parseFilenameDatePath("202508081711_raw.json")
   * // 결과: "rawdata/2025/08/08/17/202508081711_raw.json"
   * 
   * parseFilenameDatePath("invalid_file.json")
   * // 결과: null
   */
  parseFilenameDatePath(filename: string): string | null {
    // 정규식 설명:
    // ^(\d{4})     - 시작부터 4자리 숫자 (년도: 2025)
    // (\d{2})      - 2자리 숫자 (월: 08)
    // (\d{2})      - 2자리 숫자 (일: 08)
    // (\d{2})      - 2자리 숫자 (시간: 17)
    // (\d{2})      - 2자리 숫자 (분: 11)
    // _(raw|rawdata)\.json$ - "_raw.json" 또는 "_rawdata.json"으로 끝남
    const match = filename.match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})_(raw|rawdata)\.json$/);
    
    // 정규식 매치 실패시 null 반환 (잘못된 파일명 형식)
    if (!match) {
      console.log(`파일명 파싱 실패: ${filename} (YYYYMMDDHHMM_raw.json 또는 YYYYMMDDHHMM_rawdata.json 패턴이 아님)`);
      return null;
    }
    
    // 정규식 캡처 그룹에서 날짜 정보 추출 (분은 경로에 사용하지 않음)
    // match[0] = 전체 매치, match[1] = 첫 번째 캡처 그룹, ...
    const [, year, month, day, hour] = match;
    
    // S3 디렉토리 구조에 맞춰 경로 생성
    // 패턴: rawdata/YYYY/MM/DD/HH/원본파일명.json
    const s3Path = `rawdata/${year}/${month}/${day}/${hour}/${filename}`;
    console.log(`파일명 파싱 성공: ${filename} → ${s3Path}`);
    
    return s3Path;
  }

  /**
   * 파일명으로 S3에서 파일을 지능적으로 검색
   * 
   * 검색 전략:
   * 1. 우선순위 1: 파일명 패턴 분석 → 정확한 S3 경로 생성 → 빠른 확인
   * 2. 우선순위 2: 전체 rawdata 폴더 스캔 → 파일명 매칭 → 느리지만 확실
   * 
   * 이 2단계 접근법의 장점:
   * - 대부분의 경우 1단계에서 빠르게 찾음 (O(1) 성능)
   * - 예외 상황에서도 2단계로 안전하게 찾음 (O(n) 성능)
   * 
   * @param filename 검색할 파일명 (예: "202508081711_raw.json")
   * @returns 찾은 파일의 전체 S3 키 또는 null
   * 
   * @example
   * await findFileByName("202508081711_raw.json")
   * // 결과: "rawdata/2025/08/08/14/202508081711_raw.json"
   */
  async findFileByName(filename: string): Promise<string | null> {
    console.log(`파일 검색 시작: ${filename}`);
    
    // === 1단계: 스마트 검색 (파일명 패턴 분석) ===
    // 파일명이 날짜 패턴(YYYYMMDDHHMM_raw.json)을 따르는지 확인
    console.log('1단계: 파일명 패턴 분석 중...');
    const parsedPath = this.parseFilenameDatePath(filename);
    
    if (parsedPath) {
      console.log(`파싱된 경로로 직접 확인: ${parsedPath}`);
      
      // HeadObject로 파일 존재 여부만 확인 (빠르고 비용 효율적)
      const exists = await this.checkFileExists(parsedPath);
      if (exists) {
        console.log(`✅ 1단계에서 파일 발견: ${parsedPath}`);
        return parsedPath;
      } else {
        console.log(`❌ 파싱된 경로에 파일 없음: ${parsedPath}`);
      }
    }

    // === 2단계: 전체 스캔 검색 (Fallback) ===
    // 1단계에서 실패했거나 패턴이 맞지 않는 경우
    console.log('2단계: rawdata 폴더 전체 스캔 중...');
    
    try {
      // rawdata 프리픽스로 시작하는 모든 객체 조회
      const command = new ListObjectsV2Command({
        Bucket: this.bucket,
        Prefix: 'rawdata/',        // rawdata/로 시작하는 모든 파일
        MaxKeys: 1000,            // 성능을 위해 최대 1000개로 제한
      });

      const response = await this.s3.send(command);
      const objects = response.Contents || [];
      
      console.log(`스캔된 객체 수: ${objects.length}개`);

      // 각 S3 객체의 키에서 파일명 부분만 추출하여 비교
      for (const obj of objects) {
        if (obj.Key && obj.Key.endsWith(`/${filename}`)) {
          console.log(`✅ 2단계에서 파일 발견: ${obj.Key}`);
          return obj.Key;
        }
      }
      
      console.log(`❌ 전체 스캔에서도 파일을 찾지 못함: ${filename}`);
    } catch (error) {
      console.error('S3 파일 검색 중 오류 발생:', error);
      // 에러가 발생해도 null을 반환하여 상위 레벨에서 적절히 처리하도록 함
    }

    return null; // 모든 시도에서 실패
  }

  /**
   * 날짜 범위로 S3 파일들 검색
   * @param startDate 시작 날짜 (YYYY-MM-DD)
   * @param endDate 종료 날짜 (YYYY-MM-DD)
   * @returns 찾은 파일들의 키 배열
   */
  async findFilesByDateRange(startDate: string, endDate?: string): Promise<string[]> {
    const start = new Date(startDate);
    const end = endDate ? new Date(endDate) : start;
    
    if (isNaN(start.getTime()) || isNaN(end.getTime())) {
      throw new Error('Invalid date format. Use YYYY-MM-DD');
    }

    const foundFiles: string[] = [];
    const currentDate = new Date(start);

    // 날짜별로 검색
    while (currentDate <= end) {
      const year = currentDate.getFullYear();
      const month = String(currentDate.getMonth() + 1).padStart(2, '0');
      const day = String(currentDate.getDate()).padStart(2, '0');
      
      const prefix = `rawdata/${year}/${month}/${day}/`;
      
      try {
        const command = new ListObjectsV2Command({
          Bucket: this.bucket,
          Prefix: prefix,
          MaxKeys: 1000,
        });

        const response = await this.s3.send(command);
        const objects = response.Contents || [];

        for (const obj of objects) {
          if (obj.Key && obj.Key.endsWith('.json')) {
            foundFiles.push(obj.Key);
          }
        }
      } catch (error) {
        console.error(`Error searching files for date ${year}-${month}-${day}:`, error);
      }

      currentDate.setDate(currentDate.getDate() + 1);
    }

    return foundFiles.sort();
  }

  /**
   * 날짜 문자열(YYYYMMDD)로 해당 날짜의 모든 시간대 JSON 파일 조회
   * 
   * 🕐 검색 방식:
   * - YYYYMMDD 형식의 날짜로 해당 날짜의 모든 시간대(00~23) 폴더를 검색
   * - 각 시간 폴더에서 JSON 파일들을 수집하여 통합 반환
   * 
   * @param dateString 날짜 문자열 (예: "20250808")
   * @param folderType 검색할 폴더 타입 ('rawdata' | 'houravg' | 'minavg')
   * @returns 해당 날짜의 모든 JSON 파일 키 배열 (시간순 정렬)
   * 
   * @example
   * await getFilesByDate("20250808", "rawdata")
   * // 결과: [
   * //   "rawdata/2025/08/08/00/202508080000_raw.json",
   * //   "rawdata/2025/08/08/01/202508080100_raw.json",
   * //   ...
   * //   "rawdata/2025/08/08/23/202508082300_raw.json"
   * // ]
   */
  async getFilesByDate(dateString: string, folderType: 'rawdata' | 'houravg' | 'minavg' = 'rawdata'): Promise<string[]> {
    // 날짜 형식 검증
    if (!/^\d{8}$/.test(dateString)) {
      throw new Error('Invalid date format. Use YYYYMMDD (e.g., 20250808)');
    }

    const year = dateString.substring(0, 4);
    const month = dateString.substring(4, 6);
    const day = dateString.substring(6, 8);
    
    const allFiles: string[] = [];
    
    // 모든 시간대(00~23) 검색
    for (let hour = 0; hour < 24; hour++) {
      const hourStr = hour.toString().padStart(2, '0');
      const prefix = `${folderType}/${year}/${month}/${day}/${hourStr}/`;
      
      try {
        const command = new ListObjectsV2Command({
          Bucket: this.bucket,
          Prefix: prefix,
          MaxKeys: 1000,
        });

        const response = await this.s3.send(command);
        const objects = response.Contents || [];

        const hourFiles = objects
          .filter(obj => obj.Key && obj.Key.endsWith('.json'))
          .map(obj => obj.Key!);

        allFiles.push(...hourFiles);
      } catch (error) {
        console.error(`Error getting files for ${prefix}:`, error);
        // 개별 시간대 에러는 무시하고 계속 진행
      }
    }
    
    return allFiles.sort();
  }
}