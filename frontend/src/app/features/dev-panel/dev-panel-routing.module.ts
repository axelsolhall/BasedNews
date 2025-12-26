import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';

import { DevPanelPageComponent } from './dev-panel.page';

const routes: Routes = [
  {
    path: '',
    component: DevPanelPageComponent
  }
];

@NgModule({
  imports: [RouterModule.forChild(routes)],
  exports: [RouterModule]
})
export class DevPanelRoutingModule {}
