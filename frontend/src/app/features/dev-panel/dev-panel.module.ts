import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';

import { DevPanelRoutingModule } from './dev-panel-routing.module';
import { DevPanelPageComponent } from './dev-panel.page';
import { SharedModule } from '../../shared/shared.module';

@NgModule({
  declarations: [DevPanelPageComponent],
  imports: [CommonModule, DevPanelRoutingModule, SharedModule]
})
export class DevPanelModule {}
