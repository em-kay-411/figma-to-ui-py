import { Component, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { ContextMenuModule } from 'primeng/contextmenu';

@Component({
  selector: 'app-generated-component',
  standalone: true,
  imports: [CommonModule, ButtonModule, CardModule, ContextMenuModule],
  templateUrl: './generated-component.component.html',
  styleUrls: ['./generated-component.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class GeneratedComponent { }
