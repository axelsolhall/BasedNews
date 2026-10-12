import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { SharedModule } from '../../shared/shared.module';
import { PlanePage } from './plane.page';

@NgModule({
  declarations: [PlanePage],
  imports: [CommonModule, FormsModule, SharedModule, RouterModule.forChild([{ path: '', component: PlanePage }])]
})
export class PlaneModule {}
