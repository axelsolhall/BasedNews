import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';

import { BiasPlaneComponent } from './ui/bias-plane/bias-plane.component';

@NgModule({
  declarations: [BiasPlaneComponent],
  imports: [CommonModule],
  exports: [BiasPlaneComponent]
})
export class SharedModule {}
