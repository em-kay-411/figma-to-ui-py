import { Component, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';

@Component({
  selector: 'app-generated-component',
  standalone: true,
  imports: [CommonModule, MatCardModule, MatButtonModule],
  templateUrl: './generated-component.component.html',
  styleUrls: ['./generated-component.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class GeneratedComponent { }
