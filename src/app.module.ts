import { Module } from '@nestjs/common';
import { HealthController } from './health.controller';
import { S3Module } from './s3/s3.module';
import { S3Controller } from './s3/s3.controller';
import { AppController } from './app.controller';
import { AppService } from './app.service';

@Module({
  imports: [S3Module],
  controllers: [AppController, HealthController, S3Controller],
  providers: [AppService],
})
export class AppModule {}
