import copy,sys,subprocess,os,uuid,re
from pathlib import Path
from PySide6.QtCore import Qt,QUrl,QTimer
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QSlider,QCheckBox,QComboBox,QLineEdit,QFileDialog,QMessageBox,QTableWidget,QTableWidgetItem,QHeaderView,QSpinBox,QDoubleSpinBox,QGroupBox,QInputDialog,QDialog,QDialogButtonBox,QScrollArea,QProgressBar)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtGui import QShortcut,QKeySequence,QPixmap,QImage,QPainter,QPen,QColor
from project import new_project,load,save,add_event,calculate,sorted_events,parse_timestamp,timestamp,TEAMS,state_after,attach_logo,load_default_logo
from graphics import draw_score,title_card
from replays import validate_replays


class CropFrame(QLabel):
 """Click/drag to position the centered crop, with guaranteed in-frame bounds."""
 def __init__(self):
  super().__init__();self.setAlignment(Qt.AlignCenter)
  self.setMinimumSize(400,220);self.setStyleSheet('background:#15253b;color:white')
  self.framepix=None;self.cx=.5;self.cy=.5;self.zoom=2.0
 def set_frame(self,pix):
  self.framepix=pix;self.update()
 def set_zoom(self,z):self.zoom=float(z);self.update()
 def _image_rect(self):
  if self.framepix is None or self.framepix.isNull():return None
  w,h=self.framepix.width(),self.framepix.height()
  scale=min(self.width()/w,self.height()/h)
  iw,ih=w*scale,h*scale
  return (self.width()/2-iw/2,self.height()/2-ih/2,iw,ih)
 def _move_to(self,event):
  rect=self._image_rect()
  if not rect:return
  x,y,w,h=rect
  self.cx=max(0,min(1,(event.position().x()-x)/w))
  self.cy=max(0,min(1,(event.position().y()-y)/h))
  self.update()
 def mousePressEvent(self,event):
  if event.button()==Qt.LeftButton:self._move_to(event)
 def mouseMoveEvent(self,event):
  if event.buttons() & Qt.LeftButton:self._move_to(event)
 def paintEvent(self,event):
  super().paintEvent(event)
  rect=self._image_rect()
  if not rect:return
  x,y,w,h=rect
  painter=QPainter(self);painter.setRenderHint(QPainter.SmoothPixmapTransform)
  painter.drawPixmap(int(x),int(y),int(w),int(h),self.framepix)
  cropw,croph=w/self.zoom,h/self.zoom
  left=max(x,min(x+w-cropw,x+self.cx*w-cropw/2))
  top=max(y,min(y+h-croph,y+self.cy*h-croph/2))
  painter.fillRect(int(x),int(y),int(w),int(h),QColor(0,0,0,65))
  painter.drawPixmap(int(left),int(top),int(cropw),int(croph),self.framepix,
                     int((left-x)/w*self.framepix.width()),int((top-y)/h*self.framepix.height()),
                     int(cropw/w*self.framepix.width()),int(croph/h*self.framepix.height()))
  pen=QPen(QColor('#5df2a0'));pen.setWidth(3);painter.setPen(pen)
  painter.drawRect(int(left),int(top),int(cropw),int(croph))
  painter.end()

class CropDialog(QDialog):
 def __init__(self,frame,cx,cy,zoom,parent=None):
  super().__init__(parent);self.setWindowTitle('Choose replay zoom area (drag or click)');self.resize(850,560)
  layout=QVBoxLayout(self);layout.addWidget(QLabel('Drag the green crop rectangle to keep the action in frame. The crop is clamped to the video edges.'))
  self.crop=CropFrame();self.crop.cx=cx;self.crop.cy=cy;self.crop.zoom=zoom
  if frame is not None:self.crop.set_frame(frame)
  else:self.crop.setText('No video frame available; zoom center defaults to center. Seek to a frame and try again.')
  layout.addWidget(self.crop,1)
  row=QHBoxLayout();layout.addLayout(row);row.addWidget(QLabel('Zoom:'))
  self.zoom=QDoubleSpinBox();self.zoom.setRange(1,5);self.zoom.setSingleStep(.25);self.zoom.setValue(zoom)
  self.zoom.valueChanged.connect(self.crop.set_zoom);row.addWidget(self.zoom);row.addStretch()
  buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
  buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

class Window(QMainWindow):
 def __init__(self):
  super().__init__();self.setWindowTitle('Volleyball Score Annotator');self.resize(1400,900)
  self.project=new_project();self.path=None;self.selected_id=None;self.undo_stack=[];self.syncing=False;self.render_process=None;self.render_log=None
  self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.audio.setVolume(.7);self.player.setAudioOutput(self.audio)
  root=QWidget();self.setCentralWidget(root);main=QVBoxLayout(root)
  top=QHBoxLayout();main.addLayout(top)
  for label,func in [('New',self.new),('Open project',self.open),('Save as',self.save_as),('Choose video',self.choose_video),('Export video',self.export),('Export selected set',self.export_selected_set),('Title card PNG',self.export_title)]:
   btn=QPushButton(label);btn.clicked.connect(func);top.addWidget(btn)
  top.addStretch()
  self.encoder=QComboBox();self.encoder.addItem('Auto (NVENC → CPU)','auto');self.encoder.addItem('NVIDIA NVENC','nvenc');self.encoder.addItem('CPU (libx264)','cpu')
  top.addWidget(QLabel('Export encoder:'));top.addWidget(self.encoder)
  self.export_progress=QProgressBar();self.export_progress.setRange(0,100);self.export_progress.setValue(0);self.export_progress.setFixedWidth(160);self.export_progress.hide()
  self.statusBar().addPermanentWidget(self.export_progress)
  body=QHBoxLayout();main.addLayout(body,1);left=QVBoxLayout();body.addLayout(left,3);right=QVBoxLayout();body.addLayout(right,2)
  self.video=QVideoWidget();self.player.setVideoOutput(self.video);left.addWidget(self.video,5)
  seek=QHBoxLayout();left.addLayout(seek)
  self.play_btn=QPushButton('▶ Play / Pause');self.play_btn.clicked.connect(self.toggle);seek.addWidget(self.play_btn)
  self.slider=QSlider(Qt.Horizontal);self.slider.setRange(0,1000);self.slider.sliderMoved.connect(self.player.setPosition);seek.addWidget(self.slider,1)
  self.time_label=QLabel('00:00:00.000 / 00:00:00.000');seek.addWidget(self.time_label)
  self.player.positionChanged.connect(self.update_position);self.player.durationChanged.connect(lambda _:self.slider.setRange(0,max(1,self.player.duration())))
  speed=QHBoxLayout();left.addLayout(speed);speed.addWidget(QLabel('Playback speed:'))
  self.speed=QComboBox();self.speed.addItems(['0.5×','0.75×','1×','1.25×','1.5×','2×']);self.speed.setCurrentText('1×');self.speed.currentTextChanged.connect(lambda x:self.player.setPlaybackRate(float(x[:-1])));speed.addWidget(self.speed)
  self.auto_pause=QCheckBox('Pause when a point is marked');self.auto_pause.setChecked(False);speed.addWidget(self.auto_pause);speed.addStretch()
  actions=QHBoxLayout();left.addLayout(actions)
  for lab,func in [('+ Point left (A)',lambda:self.add_point('home')),('+ Point right (L)',lambda:self.add_point('away')),('End set (N)',self.end_set),('Undo (Z)',self.undo)]:
   b=QPushButton(lab);b.setMinimumHeight(48);b.clicked.connect(func);actions.addWidget(b)
  self.score_label=QLabel('0 – 0  |  Set 1  |  Sets 0 – 0');self.score_label.setAlignment(Qt.AlignCenter);left.addWidget(self.score_label)
  self.preview=QLabel();self.preview.setAlignment(Qt.AlignCenter);self.preview.setMinimumHeight(140);left.addWidget(self.preview)
  details=QGroupBox('Match metadata');right.addWidget(details);form=QFormLayout(details)
  self.name_home=QComboBox();self.name_away=QComboBox()
  for cb in (self.name_home,self.name_away):cb.setEditable(True);cb.addItems(TEAMS)
  self.name_away.setCurrentText('Aces')
  form.addRow('Team A (score left)',self.name_home);form.addRow('Team B (score right)',self.name_away)
  self.home_logo=QLineEdit();self.away_logo=QLineEdit();
  for title,edit in [('Team A logo',self.home_logo),('Team B logo',self.away_logo)]:
   r=QHBoxLayout();r.addWidget(edit);b=QPushButton('Browse');b.clicked.connect(lambda _, e=edit:self.pick_logo(e));r.addWidget(b);form.addRow(title,r)
  self.match_title=QLineEdit('Playoffs');form.addRow('Title card text',self.match_title)
  self.home_court=QComboBox();self.home_court.addItems(['left','right']);form.addRow('Team A physical side',self.home_court)
  self.duration=QDoubleSpinBox();self.duration.setRange(1,40);self.duration.setValue(15);form.addRow('Overlay seconds (legacy)',self.duration);self.duration.setEnabled(False)
  self.fade=QDoubleSpinBox();self.fade.setRange(0,5);self.fade.setSingleStep(.1);self.fade.setValue(1);form.addRow('Fade seconds (legacy)',self.fade);self.fade.setEnabled(False)
  form.addRow(QLabel('Exports now keep the scoreboard visible throughout play.'))
  self.position=QComboBox();self.position.addItems(['bottom_left','bottom_right','top_left','top_right']);form.addRow('Overlay position',self.position)
  self.width=QSpinBox();self.width.setRange(360,1200);self.width.setValue(550);form.addRow('Overlay width (px)',self.width)
  self.margin=QSpinBox();self.margin.setRange(0,200);self.margin.setValue(28);form.addRow('Margin (px)',self.margin)
  for w in (self.name_home,self.name_away):w.currentTextChanged.connect(self.team_changed)
  for w in (self.home_logo,self.away_logo,self.match_title):w.editingFinished.connect(self.meta_changed)
  for w in (self.home_court,self.position):w.currentTextChanged.connect(self.meta_changed)
  for w in (self.duration,self.fade,self.width,self.margin):w.valueChanged.connect(self.meta_changed)
  right.addWidget(QLabel('Scoring events (select to edit or seek)'))
  self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Time','Type','Team','Score','Set']);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.table.setSelectionBehavior(QTableWidget.SelectRows);self.table.setEditTriggers(QTableWidget.NoEditTriggers);self.table.itemSelectionChanged.connect(self.on_selection);right.addWidget(self.table,1)
  edits=QHBoxLayout();right.addLayout(edits)
  self.time_edit=QLineEdit('00:00:00.000');edits.addWidget(self.time_edit)
  self.team_edit=QComboBox();self.team_edit.addItems(['home','away']);edits.addWidget(self.team_edit)
  for name,func in [('Apply edit',self.edit_event),('Delete event',self.delete_event),('Jump',self.jump)]:
   b=QPushButton(name);b.clicked.connect(func);edits.addWidget(b)
  sets_group=QGroupBox('Set video ranges (original recording timestamps)');right.addWidget(sets_group)
  sl=QVBoxLayout(sets_group)
  rr=QHBoxLayout();sl.addLayout(rr)
  self.set_number=QComboBox();self.set_number.addItems(['1','2','3','4','5']);self.set_number.currentTextChanged.connect(self.show_set_range)
  rr.addWidget(QLabel('Set:'));rr.addWidget(self.set_number)
  self.set_start=QLineEdit('');self.set_end=QLineEdit('')
  self.set_start.setPlaceholderText('Start HH:MM:SS.mmm');self.set_end.setPlaceholderText('End HH:MM:SS.mmm')
  rr.addWidget(self.set_start,1);rr.addWidget(self.set_end,1)
  rr=QHBoxLayout();sl.addLayout(rr)
  for label,callback in [('Mark start',self.mark_set_start),('Mark end',self.mark_set_end),('Save range',self.save_set_range),('Clear range',self.clear_set_range)]:
   b=QPushButton(label);b.clicked.connect(callback);rr.addWidget(b)
  rr=QHBoxLayout();sl.addLayout(rr)
  self.output_size=QComboBox();self.output_size.addItem('Original resolution (4K if source is 4K)',0);self.output_size.addItem('1080p (faster / smaller)',1080);self.output_size.addItem('720p (fast preview)',720)
  self.output_size.setCurrentIndex(1);rr.addWidget(QLabel('Set export:'));rr.addWidget(self.output_size,1)
  self.set_title=QCheckBox('Include 4-second title (slower)');rr.addWidget(self.set_title)
  replay=QGroupBox('Instant replays (inserted into final export)');right.addWidget(replay)
  replay_layout=QVBoxLayout(replay)
  mark=QHBoxLayout();replay_layout.addLayout(mark)
  for text,func in [('Mark IN',self.mark_replay_in),('Mark OUT',self.mark_replay_out),('Set insert',self.mark_replay_insert)]:
   button=QPushButton(text);button.clicked.connect(func);mark.addWidget(button)
  row=QHBoxLayout();replay_layout.addLayout(row)
  self.replay_in=QLineEdit('00:00:00.000');self.replay_out=QLineEdit('00:00:03.000');self.replay_insert=QLineEdit('00:00:03.000')
  for text,edit in [('In',self.replay_in),('Out',self.replay_out),('Insert at',self.replay_insert)]:
   col=QVBoxLayout();col.addWidget(QLabel(text));col.addWidget(edit);row.addLayout(col)
  row=QHBoxLayout();replay_layout.addLayout(row)
  row.addWidget(QLabel('Speed'))
  self.replay_speed=QComboBox();self.replay_speed.addItems(['0.05×','0.1×','0.125×','0.2×','0.25×','0.5×','0.75×','1×']);self.replay_speed.setCurrentText('0.5×');row.addWidget(self.replay_speed)
  row.addWidget(QLabel('Zoom'))
  self.replay_zoom=QDoubleSpinBox();self.replay_zoom.setRange(1,5);self.replay_zoom.setSingleStep(.25);self.replay_zoom.setValue(2);row.addWidget(self.replay_zoom)
  self.replay_cx=.5;self.replay_cy=.5
  button=QPushButton('Select crop on current frame');button.clicked.connect(self.pick_replay_crop);replay_layout.addWidget(button)
  row=QHBoxLayout();replay_layout.addLayout(row)
  for text,func in [('Add replay',self.add_replay),('Update selected',self.update_replay),('Remove',self.delete_replay)]:
   button=QPushButton(text);button.clicked.connect(func);row.addWidget(button)
  self.replay_table=QTableWidget(0,4);self.replay_table.setHorizontalHeaderLabels(['Insert','Source clip','Speed','Zoom'])
  self.replay_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
  self.replay_table.setMaximumHeight(170);self.replay_table.setSelectionBehavior(QTableWidget.SelectRows)
  self.replay_table.setEditTriggers(QTableWidget.NoEditTriggers)
  self.replay_table.itemSelectionChanged.connect(self.replay_selected)
  replay_layout.addWidget(self.replay_table)
  self.statusBar().showMessage('New project. Open a local video to begin. Keyboard: A / L score, Space play, N end set, Z undo, ←/→ seek.')
  for key,func in [('A',lambda:self.add_point('home')),('L',lambda:self.add_point('away')),('N',self.end_set),('Z',self.undo),('Space',self.toggle),('Left',lambda:self.seek(-2000)),('Right',lambda:self.seek(2000))]:
   sh=QShortcut(QKeySequence(key),self);sh.setContext(Qt.WindowShortcut);sh.activated.connect(lambda f=func: f() if not QApplication.focusWidget() or not isinstance(QApplication.focusWidget(),(QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox)) else None)
  self.refresh()
  self.refresh_replays()
  self.show_set_range()
 def meta_changed(self,*_):
  if self.syncing:return
  self.capture_meta();self.autosave();self.refresh_score()
 def capture_meta(self):
  p=self.project
  for key,cb in [('home',self.name_home),('away',self.name_away)]:p['teams'][key]['name']=cb.currentText()
  p['court_sides']={'home':self.home_court.currentText(),'away':'right' if self.home_court.currentText()=='left' else 'left'}
  p['match_title']=self.match_title.text();p['overlay'].update({'duration':self.duration.value(),'fade':self.fade.value(),'position':self.position.currentText(),'width':self.width.value(),'margin':self.margin.value()})
 def team_changed(self,*_):
  if self.syncing:return
  for key,cb,edit in [('home',self.name_home,self.home_logo),('away',self.name_away,self.away_logo)]:
   team=self.project['teams'][key]
   if team.get('name')!=cb.currentText():
    team['name']=cb.currentText();team.pop('logo_data',None)
    load_default_logo(team)
    edit.setText('Embedded in JSON' if team.get('logo_data') else '')
  self.capture_meta();self.autosave();self.refresh_score()
 def show_meta(self):
  self.syncing=True;p=self.project;o=p['overlay']
  for key,cb,edit in [('home',self.name_home,self.home_logo),('away',self.name_away,self.away_logo)]:cb.setCurrentText(p['teams'][key]['name']);edit.setText('Embedded in JSON' if p['teams'][key].get('logo_data') else p['teams'][key].get('logo',''))
  self.match_title.setText(p.get('match_title','Playoffs'));self.home_court.setCurrentText(p.get('court_sides',{}).get('home','left'))
  for widget,key in [(self.duration,'duration'),(self.fade,'fade'),(self.width,'width'),(self.margin,'margin')]:widget.setValue(o.get(key,15 if key=='duration' else 1 if key=='fade' else 550 if key=='width' else 28))
  self.position.setCurrentText(o.get('position','bottom_left'));self.syncing=False
 def autosave(self):
  if self.path:save(self.path,self.project)
 def new(self):
  path,_=QFileDialog.getSaveFileName(self,'New project','match.json','JSON (*.json)')
  if not path:return
  self.project=new_project();self.path=path;self.show_meta();self.autosave();self.refresh();self.refresh_replays();self.show_set_range();self.player.setSource(QUrl())
 def open(self):
  path,_=QFileDialog.getOpenFileName(self,'Open project','','JSON (*.json)')
  if not path:return
  try:self.project=load(path);self.path=path;self.undo_stack=[];self.show_meta();self.open_video(self.project.get('video',''));self.refresh();self.refresh_replays();self.show_set_range()
  except Exception as e:QMessageBox.critical(self,'Cannot open',str(e))
 def save_as(self):
  path,_=QFileDialog.getSaveFileName(self,'Save project as',self.path or 'match.json','JSON (*.json)')
  if path:self.path=path;self.capture_meta();self.autosave();self.statusBar().showMessage('Saved '+path)
 def pick_logo(self,edit):
  path,_=QFileDialog.getOpenFileName(self,'Team logo','','Images (*.png *.jpg *.webp)')
  if path:
   key='home' if edit==self.home_logo else 'away'
   try:
    attach_logo(self.project['teams'][key],path);edit.setText('Embedded in JSON')
    self.autosave();self.refresh_score()
   except Exception as e:QMessageBox.warning(self,'Logo error',str(e))
 def choose_video(self):
  path,_=QFileDialog.getOpenFileName(self,'Open local video','','Videos (*.mp4 *.mkv *.mov *.avi *.webm);;All files (*)')
  if path:self.open_video(path);self.project['video']=path;self.autosave()
 def open_video(self,path):
  if path and Path(path).exists():self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
  elif path: self.statusBar().showMessage('Video not found: '+path+'; choose a local copy')
 def toggle(self):
  if self.player.playbackState()==QMediaPlayer.PlayingState:self.player.pause()
  else:self.player.play()
 def seek(self,ms):self.player.setPosition(max(0,self.player.position()+ms))
 def update_position(self,ms):
  if not self.slider.isSliderDown():self.slider.setValue(ms)
  self.time_label.setText(f'{timestamp(ms/1000)} / {timestamp(self.player.duration()/1000)}')
 def push_undo(self):self.undo_stack.append(copy.deepcopy(self.project['events']));self.undo_stack=self.undo_stack[-50:]
 def add_point(self,team):
  self.push_undo();add_event(self.project,'point',team,self.player.position()/1000);self.autosave();self.refresh()
  if self.auto_pause.isChecked():self.player.pause()
 def end_set(self):
  try:s=state_after(self.project)
  except ValueError as e:QMessageBox.warning(self,'Invalid events',str(e));return
  if s['match_over']:
   QMessageBox.information(self,'Match finished','A team already won three sets.');return
  home=s['points']['home'];away=s['points']['away']
  if home>away:default='home'
  elif away>home:default='away'
  else:default=None
  names=[self.project['teams'][k]['name'] for k in ('home','away')]
  choices=[f'{names[0]} ({home})',f'{names[1]} ({away})']
  picked,ok=QInputDialog.getItem(self,'End set',f'Set {s["set"]} winner (choose manually or accept leader):',choices,0 if default!='away' else 1,False)
  if not ok:return
  winner='home' if picked==choices[0] else 'away'
  self.push_undo();add_event(self.project,'set_end',winner,self.player.position()/1000)
  self.autosave();self.refresh()

 def undo(self):
  if self.undo_stack:self.project['events']=self.undo_stack.pop();self.autosave();self.refresh()
 def refresh(self):
  self.syncing=True
  try:snaps=calculate(self.project)
  except ValueError as e:snaps=[];self.statusBar().showMessage('Scoring validation: '+str(e))
  self.table.setRowCount(len(snaps))
  for row,s in enumerate(snaps):
   e=s['event'];values=[timestamp(e['time']),e['type'],e.get('team',e.get('winner','')),f'{s["points"]["home"]}–{s["points"]["away"]}',str(s['set'])]
   for col,v in enumerate(values):
    item=QTableWidgetItem(str(v));item.setData(Qt.UserRole,e['id']);self.table.setItem(row,col,item)
  self.syncing=False;self.refresh_score(snaps)
 def refresh_score(self,snaps=None):
  if snaps is None:
   try:snaps=calculate(self.project)
   except ValueError:return
  s=state_after(self.project)
  self.score_label.setText(f'{s["points"]["home"]} – {s["points"]["away"]}    |    Set {s["set"]}    |    Sets {s["sets"]["home"]} – {s["sets"]["away"]}')
  im=draw_score(self.project,s);q=QImage(im.tobytes(),im.width,im.height,im.width*4,QImage.Format_RGBA8888)
  self.preview.setPixmap(QPixmap.fromImage(q.copy()).scaled(550,190,Qt.KeepAspectRatio,Qt.SmoothTransformation))
 def on_selection(self):
  if self.syncing:return
  row=self.table.currentRow()
  if row<0:return
  item=self.table.item(row,0)
  if not item:return
  self.selected_id=item.data(Qt.UserRole);event=next((e for e in self.project['events'] if e['id']==self.selected_id),None)
  if event:self.time_edit.setText(timestamp(event['time']));self.team_edit.setCurrentText(event.get('team',event.get('winner','home')))
 def edit_event(self):
  e=next((x for x in self.project['events'] if x['id']==self.selected_id),None)
  if not e:return
  try:t=parse_timestamp(self.time_edit.text())
  except ValueError as ex:QMessageBox.warning(self,'Invalid time',str(ex));return
  self.push_undo();e['time']=round(t,3)
  if e['type']=='point':e['team']=self.team_edit.currentText()
  else:e['winner']=self.team_edit.currentText()
  try:calculate(self.project)
  except ValueError as ex:
   self.project['events']=self.undo_stack.pop();QMessageBox.warning(self,'Invalid scoring sequence',str(ex));return
  self.autosave();self.refresh()
 def delete_event(self):
  if not self.selected_id:return
  self.push_undo();self.project['events']=[e for e in self.project['events'] if e['id']!=self.selected_id]
  try:calculate(self.project)
  except ValueError as ex:self.project['events']=self.undo_stack.pop();QMessageBox.warning(self,'Cannot delete',str(ex));return
  self.selected_id=None;self.autosave();self.refresh()
 def jump(self):
  try:self.player.setPosition(round(parse_timestamp(self.time_edit.text())*1000))
  except ValueError as e:QMessageBox.warning(self,'Invalid time',str(e))
 def mark_replay_in(self):self.replay_in.setText(timestamp(self.player.position()/1000))
 def mark_replay_out(self):
  now=self.player.position()/1000
  self.replay_out.setText(timestamp(now));self.replay_insert.setText(timestamp(now))
 def mark_replay_insert(self):self.replay_insert.setText(timestamp(self.player.position()/1000))
 def pick_replay_crop(self):
  frame=None
  try:
   sink=self.video.videoSink()
   vf=sink.videoFrame()
   if vf.isValid():
    img=vf.toImage()
    if not img.isNull():frame=QPixmap.fromImage(img)
  except Exception as e:
   self.statusBar().showMessage('Could not capture video frame: '+str(e))
  dialog=CropDialog(frame,self.replay_cx,self.replay_cy,self.replay_zoom.value(),self)
  if dialog.exec()==QDialog.Accepted:
   self.replay_cx=dialog.crop.cx;self.replay_cy=dialog.crop.cy;self.replay_zoom.setValue(dialog.zoom.value())
 def replay_data(self):
  start=parse_timestamp(self.replay_in.text());end=parse_timestamp(self.replay_out.text())
  insert=parse_timestamp(self.replay_insert.text())
  return {'start':start,'end':end,'insert':insert,'speed':float(self.replay_speed.currentText()[:-1]),
          'zoom':self.replay_zoom.value(),'cx':self.replay_cx,'cy':self.replay_cy}
 def check_replay(self,r):
  duration=self.player.duration()/1000
  if duration<=0:
   raise ValueError('Load the video and wait for its duration to become available')
  validate_replays({'replays':[r]},duration)
 def add_replay(self):
  try:
   r=self.replay_data();r['id']=str(uuid.uuid4());self.check_replay(r)
  except Exception as e:QMessageBox.warning(self,'Invalid replay',str(e));return
  self.project.setdefault('replays',[]).append(r);self.autosave();self.refresh_replays()
 def replay_selected(self):
  if self.syncing:return
  selected=self.replay_table.selectedItems()
  if not selected:return
  ident=selected[0].data(Qt.UserRole)
  r=next((r for r in self.project.get('replays',[]) if r['id']==ident),None)
  if not r:return
  self.replay_in.setText(timestamp(r['start']));self.replay_out.setText(timestamp(r['end']))
  self.replay_insert.setText(timestamp(r['insert']))
  self.replay_speed.setCurrentText(f'{r["speed"]:g}×');self.replay_zoom.setValue(float(r['zoom']))
  self.replay_cx=r.get('cx',.5);self.replay_cy=r.get('cy',.5)
  self.player.setPosition(round(float(r['start'])*1000))
 def selected_replay_id(self):
  items=self.replay_table.selectedItems()
  return items[0].data(Qt.UserRole) if items else None
 def update_replay(self):
  ident=self.selected_replay_id()
  if not ident:return
  try:r=self.replay_data();self.check_replay(r)
  except Exception as e:QMessageBox.warning(self,'Invalid replay',str(e));return
  for old in self.project['replays']:
   if old['id']==ident:old.update(r);break
  self.autosave();self.refresh_replays()
 def delete_replay(self):
  ident=self.selected_replay_id()
  if not ident:return
  self.project['replays']=[r for r in self.project['replays'] if r['id']!=ident]
  self.autosave();self.refresh_replays()
 def refresh_replays(self):
  self.syncing=True
  rows=sorted(self.project.get('replays',[]),key=lambda r:r['insert'])
  self.replay_table.setRowCount(len(rows))
  for row,r in enumerate(rows):
   values=[timestamp(r['insert']),f'{timestamp(r["start"])} – {timestamp(r["end"])}',f'{r["speed"]:g}×',f'{r["zoom"]:g}×']
   for column,value in enumerate(values):
    item=QTableWidgetItem(value);item.setData(Qt.UserRole,r['id']);self.replay_table.setItem(row,column,item)
  self.syncing=False
 def export_title(self):
  self.capture_meta();path,_=QFileDialog.getSaveFileName(self,'Save title card','title_card.png','PNG (*.png)')
  if path:title_card(self.project).save(path);self.statusBar().showMessage('Saved '+path)
 def show_set_range(self,*_):
  entry=self.project.get('set_ranges',{}).get(self.set_number.currentText(),{})
  self.set_start.setText(timestamp(entry['start']) if 'start' in entry else '')
  self.set_end.setText(timestamp(entry['end']) if 'end' in entry else '')
 def mark_set_start(self):self.set_start.setText(timestamp(self.player.position()/1000));self.save_set_range(False)
 def mark_set_end(self):self.set_end.setText(timestamp(self.player.position()/1000));self.save_set_range(False)
 def save_set_range(self,notify=True):
  try:
   entry={}
   if self.set_start.text().strip():entry['start']=round(parse_timestamp(self.set_start.text()),3)
   if self.set_end.text().strip():entry['end']=round(parse_timestamp(self.set_end.text()),3)
   if len(entry)==2 and entry['start']>=entry['end']:raise ValueError('End must be after start')
   self.project.setdefault('set_ranges',{})[self.set_number.currentText()]=entry
   self.autosave()
   if notify:self.statusBar().showMessage('Set '+self.set_number.currentText()+' range saved')
  except ValueError as e:QMessageBox.warning(self,'Invalid set range',str(e))
 def clear_set_range(self):
  self.project.setdefault('set_ranges',{}).pop(self.set_number.currentText(),None)
  self.show_set_range();self.autosave()
 def export_selected_set(self):
  self.capture_meta()
  if not self.project.get('video') or not Path(self.project['video']).exists():
   QMessageBox.warning(self,'Missing video','Choose a local source video first.');return
  self.save_set_range(False)
  number=int(self.set_number.currentText())
  bounds=self.project.get('set_ranges',{}).get(str(number),{})
  if 'start' not in bounds or 'end' not in bounds or bounds['end']<=bounds['start']:
   QMessageBox.warning(self,'Incomplete set','Mark start and end for this set first.');return
  suggestion=f'set_{number}.mp4'
  path,_=QFileDialog.getSaveFileName(self,'Export set '+str(number),suggestion,'MP4 (*.mp4)')
  if path:self.start_export(path,['--set',str(number),'--height',str(self.output_size.currentData())]+(['--no-title'] if not self.set_title.isChecked() else []))
 def export(self):
  self.capture_meta()
  if not self.project['video'] or not Path(self.project['video']).exists():QMessageBox.warning(self,'Missing video','Choose a local source video first.');return
  path,_=QFileDialog.getSaveFileName(self,'Export annotated video','annotated.mp4','MP4 (*.mp4)')
  if path:self.start_export(path,[])
 def start_export(self,path,extra):
  if self.render_process and self.render_process.poll() is None:
   QMessageBox.information(self,'Export running','An export is already in progress.');return
  try:
   if not self.path:self.path=str(Path(path).with_suffix('.json'))
   self.autosave()
   cmd=[sys.executable,'-u',str(Path(__file__).with_name('renderer.py')),self.path,path,'--encoder',self.encoder.currentData(),*extra]
   log_path=Path(path).with_suffix('.render.log')
   self.render_log=open(log_path,'w',encoding='utf8')
   flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
   self.render_process=subprocess.Popen(cmd,stdout=self.render_log,stderr=subprocess.STDOUT,creationflags=flags)
   self.render_output=path;self.render_log_path=log_path
   self.render_progress_offset=0;self.render_progress_partial=b'';self.render_stage='Preparing';self.render_expected=0.;self.render_seconds=0.;self.render_speed='';self.render_encoder='';self.last_advance=None
   self.export_progress.setValue(0);self.export_progress.show()
   self.statusBar().showMessage('Export running; log: '+str(log_path))
   self.render_timer=QTimer(self);self.render_timer.timeout.connect(self.check_render)
   self.render_timer.start(1000)
  except Exception as e:QMessageBox.critical(self,'Export error',str(e))

 def check_render(self):
  if not self.render_process:return
  code=self.render_process.poll()
  # Incrementally read new log lines. Older v0.4 versions lost the STAGE line
  # once it scrolled beyond the 16 KB tail, freezing the GUI near 4%.
  try:
   with self.render_log_path.open('rb') as f:
    f.seek(self.render_progress_offset)
    data=self.render_progress_partial+f.read()
    self.render_progress_offset=f.tell()
   parts=data.split(b'\n');self.render_progress_partial=parts.pop()
   for raw in parts:
    line=raw.decode('utf8','replace').strip()
    if line.startswith('STAGE|'):
     fields=line.split('|',2);self.render_stage=fields[1]
     try:self.render_expected=float(fields[2])
     except (ValueError,IndexError):self.render_expected=0.
     self.render_seconds=0.;self.render_speed='';self.export_progress.setValue(0)
    elif line.startswith('ENCODER|'):self.render_encoder=line[8:]
    elif line.startswith('speed='):self.render_speed=line.split('=',1)[1].strip()
    elif line.startswith('out_time='):
     value=line[9:].strip()
     if value!='N/A':
      try:
       new=parse_timestamp(value)
       if new>self.render_seconds:self.last_advance=__import__('time').monotonic()
       self.render_seconds=new
      except ValueError:pass
   if self.render_expected>0:
    self.export_progress.setValue(min(100,max(0,round(100*self.render_seconds/self.render_expected))))
   since=__import__('time').monotonic()-self.last_advance if self.last_advance else 0
   note=f' • no new frames for {since:.0f}s' if since>=15 else ''
   self.statusBar().showMessage(f'{self.render_encoder or "Encoding"} • {self.render_stage} • '+
                                f'{self.render_speed or "starting"} • {self.export_progress.value()}%'+note)
  except (OSError,ValueError):pass
  if code is None:return
  self.export_progress.hide()
  self.render_timer.stop();self.render_log.close();self.render_log=None;self.render_process=None
  if code==0 and Path(self.render_output).exists() and Path(self.render_output).stat().st_size>0:
   self.statusBar().showMessage('Export completed: '+self.render_output)
   QMessageBox.information(self,'Export complete','Video saved:\n'+self.render_output)
  else:
   lines=self.render_log_path.read_text(encoding='utf8',errors='replace').splitlines()
   tail='\n'.join(lines[-20:]);self.statusBar().showMessage('Export failed')
   QMessageBox.critical(self,'Export failed','FFmpeg failed. Log:\n'+str(self.render_log_path)+'\n\n'+tail[-2600:])

if __name__=='__main__':
 app=QApplication(sys.argv);w=Window();w.show();sys.exit(app.exec())
