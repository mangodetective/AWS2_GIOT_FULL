import { Controller, Get } from '@nestjs/common';

@Controller()
export class HealthController {
  /**
   * @api {GET} /healthz 애플리케이션 헬스체크
   * @apiName HealthCheck
   * @apiGroup Health
   * 
   * @apiDescription 애플리케이션의 상태를 확인하는 헬스체크 엔드포인트
   * 로드 밸런서나 모니터링 시스템에서 서비스 상태를 확인하는데 사용
   * 
   * @apiSuccess {Boolean} ok 애플리케이션 정상 상태 (항상 true)
   * 
   * @apiExample {curl} Example usage:
   *     curl -X GET http://localhost:3000/healthz
   * 
   * @apiSuccessExample {json} Success-Response:
   *     HTTP/1.1 200 OK
   *     {
   *       "ok": true
   *     }
   */
  @Get('healthz')
  health() {
    return { ok: true };
  }
}
