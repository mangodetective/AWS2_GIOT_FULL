// AWS API 서비스를 위한 메인 애플리케이션 모듈
// S3 및 QuickSight 기능 제공

import { Module } from '@nestjs/common';
import { HealthController } from './health.controller';
import { S3Module } from './s3/s3.module';
import { S3Controller } from './s3/s3.controller';
import { QuickSightModule } from './quicksight/quicksight.module';
import { AppController } from './app.controller';
import { AppService } from './app.service';

@Module({
  imports: [S3Module, QuickSightModule],
  controllers: [AppController, HealthController, S3Controller],
  providers: [AppService],
})
export class AppModule {}
