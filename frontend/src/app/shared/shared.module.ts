import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';

import { ButtonComponent } from './ui/button/button.component';
import { CardComponent } from './ui/card/card.component';
import { TableComponent } from './ui/table/table.component';
import { TagComponent } from './ui/tag/tag.component';

@NgModule({
  declarations: [
    ButtonComponent,
    CardComponent,
    TableComponent,
    TagComponent
  ],
  imports: [CommonModule],
  exports: [
    CardComponent,
    ButtonComponent,
    TableComponent,
    TagComponent
  ]
})
export class SharedModule {}