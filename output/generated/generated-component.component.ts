import { Component, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ButtonModule } from 'primeng/button';

@Component({
  selector: 'app-generated-component',
  standalone: true,
  imports: [ButtonModule, CommonModule],
  templateUrl: './generated-component.component.html',
  styleUrls: ['./generated-component.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class GeneratedComponent { }