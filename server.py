from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import sessionmaker
from Yowsup.connectionmanager import YowsupConnectionManager
from Yowsup.Common.utilities import Utilities
from Yowsup.Media.uploader import MediaUploader
import os, json, base64, time, requests, hashlib, datetime
import logging

import calendar
from datetime import datetime, timedelta
from pubnub import Pubnub

Base = declarative_base()
logging.basicConfig(filename='logs/production.log',level=logging.DEBUG, format='%(asctime)s %(message)s')


class Message(Base):
	__tablename__ = 'messages'
	id = Column(Integer, primary_key=True)
	received = Column(Boolean())

	def __init__(self, received):
		self.received = received


class Asset(Base):
	__tablename__ = 'assets'
	id = Column(Integer, primary_key=True)
	name = Column(String(255))
	asset_hash = Column(String(255))
	file_file_name = Column(String(255))
	video_file_name = Column(String(255))
	video_file_size = Column(String(255))
	mms_url = Column(String(255))
	asset_type = Column(String(255))
	file_file_size = Column(Integer)
	audio_file_name = Column(String(255))
	audio_file_size = Column(Integer)

	def __init__(self, asset_hash, mms_url):
		self.asset_hash = asset_hash
		self.mms_url = mms_url

class Job(Base):
	__tablename__ = 'job_logs'
	id = Column(Integer, primary_key=True)

	method = Column(String(255))
	targets = Column(String(255))
	args = Column(String(255))
	sent = Column(Boolean())
	scheduled_time = Column(DateTime())
	simulate = Column(Boolean())
	whatsapp_message_id = Column(String(255))
	received = Column(String(255))
	receipt_timestamp = Column(DateTime())
	message_id = Column(Integer)

	def __init__(self, method, targets, sent, args, scheduled_time):
		self.method = method
		self.targets = targets
		self.sent = sent
		self.args = args
		self.scheduled_time = scheduled_time

class Server:
	def __init__(self, url, keepAlive = False, sendReceipts = False):
		self.sendReceipts = sendReceipts
		self.keepAlive = keepAlive
		self.db = create_engine(url, echo=False)

		self.Session = sessionmaker(bind=self.db)
		self.s = self.Session()

		self.pubnub = Pubnub(os.environ['PUB_KEY'], os.environ['SUB_KEY'], None, False)


		connectionManager = YowsupConnectionManager()
		connectionManager.setAutoPong(keepAlive)		

		self.signalsInterface = connectionManager.getSignalsInterface()
		self.methodsInterface = connectionManager.getMethodsInterface()
		
		self.signalsInterface.registerListener("message_received", self.onMessageReceived)
		self.signalsInterface.registerListener("group_messageReceived", self.onGroupMessageReceived)
		self.signalsInterface.registerListener("image_received", self.onImageReceived)
		self.signalsInterface.registerListener("video_received", self.onVideoReceived)
		self.signalsInterface.registerListener("audio_received", self.onAudioReceived)
		self.signalsInterface.registerListener("vcard_received", self.onVCardReceived)
		self.signalsInterface.registerListener("receipt_messageSent", self.onReceiptMessageSent)
		self.signalsInterface.registerListener("receipt_messageDelivered", self.onReceiptMessageDelivered)

		
		
		self.signalsInterface.registerListener("auth_success", self.onAuthSuccess)
		self.signalsInterface.registerListener("auth_fail", self.onAuthFailed)
		self.signalsInterface.registerListener("disconnected", self.onDisconnected)

		self.signalsInterface.registerListener("contact_gotProfilePicture", self.onGotProfilePicture)
		self.signalsInterface.registerListener("profile_setStatusSuccess", self.onSetStatusSuccess)
		self.signalsInterface.registerListener("group_createSuccess", self.onGroupCreateSuccess)
		self.signalsInterface.registerListener("group_createFail", self.onGroupCreateFail)
		self.signalsInterface.registerListener("group_gotInfo", self.onGroupGotInfo)
		self.signalsInterface.registerListener("group_addParticipantsSuccess", self.onGroupAddParticipantsSuccess)
		self.signalsInterface.registerListener("group_subjectReceived", self.onGroupSubjectReceived)
		self.signalsInterface.registerListener("notification_removedFromGroup", self.onNotificationRemovedFromGroup)
		self.signalsInterface.registerListener("group_gotParticipants", self.onGotGroupParticipants)
		


		self.signalsInterface.registerListener("media_uploadRequestSuccess", self.onUploadRequestSuccess)
		# self.signalsInterface.registerListener("media_uploadRequestFailed", self.onUploadRequestFailed)
		self.signalsInterface.registerListener("media_uploadRequestDuplicate", self.onUploadRequestDuplicate)
		self.signalsInterface.registerListener("presence_available", self.onPresenceAvailable)
		
		self.cm = connectionManager
		self.url = os.environ['URL']

		self.post_headers = {'Content-type': 'application/json', 'Accept': 'application/json'}		
		self.done = False

	def onUploadFailed(self, hash):
		print "Upload failed"
	

	def login(self, username, password):
		logging.info('In Login')
		self.username = username
		self.password = password

		self.pubnub_channel = os.environ['PUB_CHANNEL'] + "_%s" %self.username
		self.methodsInterface.call("auth_login", (username, self.password))
		self.methodsInterface.call("presence_sendAvailable", ())

		while not self.done:
			self.seekJobs()
			time.sleep(2)
	
	def seekJobs(self):
		jobs = self.s.query(Job).filter_by(sent=False).all()
		if len(jobs) > 0:
			logging.info("Pending Jobs %s" % len(jobs))

		for job in jobs:
			if self._onSchedule(job.scheduled_time):
				if job.method == "profile_setStatus":
					self.methodsInterface.call(job.method.encode('utf8'), (job.args.encode('utf8'),))
					job.sent = True
				elif job.method == "group_create":
					self.methodsInterface.call(job.method.encode('utf8'), (job.args.encode('utf8'),))
					job.sent = True
				elif job.method == "group_addParticipants":
					params = job.args.encode('utf8').split(",")
					self.methodsInterface.call(job.method.encode('utf8'), (params[0], [params[1] + "@s.whatsapp.net"],))
					job.sent = True
				elif job.method == "group_getParticipants":				
					self.methodsInterface.call('group_getParticipants', (job.targets.encode('utf8'),))
					job.sent = True
				elif job.method == "contact_getProfilePicture":
					self.methodsInterface.call("contact_getProfilePicture", (job.args.encode('utf8'),))
					job.sent = True
				elif job.method == "sendMessage":
					
					if job.simulate == True:
						self.methodsInterface.call("typing_send", (job.targets,))
						self.methodsInterface.call("typing_paused", (job.targets,))

					job.whatsapp_message_id = self.sendMessage(job.targets.encode('utf8'), job.args.encode('utf8'))
					job.sent = True
				elif job.method == "broadcast_Text":
					jids = job.targets.split(",")
					targets = []
					for jid in jids:
						targets.append("%s@s.whatsapp.net" %jid)
					self.methodsInterface.call("message_broadcast", (targets, job.args, ))

					job.sent = True
				elif job.method == "broadcast_Image":
					args = job.args.encode('utf8').split(",")
					asset_id = args[0]
					asset = self.s.query(Asset).get(asset_id)
					jids = job.targets.split(",")
					for jid in jids:
						self.sendImage(jid + "@s.whatsapp.net", asset)
						time.sleep(1)
					job.sent = True
				elif job.method == "uploadMedia":
					args = job.args.encode('utf8').split(",")
					asset_id = args[0]
					url = args[1]
					preview = args[2]
					logging.info("Asset Id: %s" %args[0])
					asset = self.s.query(Asset).get(asset_id)				
					logging.info("File name: %s" %asset.file_file_name)
					logging.info("Video name: %s" %asset.video_file_name)
					logging.info("Url: %s" %asset.mms_url)

					if asset.mms_url == None:
						self.requestMediaUrl(url, asset, preview)
					job.sent = True
				elif job.method == "uploadAudio":
					args = job.args.encode('utf8').split(",")
					asset_id = args[0]
					url = args[1]
					logging.info("Asset Id: %s" %args[0])
					asset = self.s.query(Asset).get(asset_id)
					logging.info("File name: %s" %asset.audio_file_name)

					if asset.mms_url == None:
						self.requestMediaUrl(url, asset, None)
					job.sent = True
				elif job.method == "sendImage":
					asset = self._getAsset(job.args)
					jids = job.targets.split(",")
					for jid in jids:
						self.sendImage(jid + "@s.whatsapp.net", asset)
					job.sent = True
				elif job.method == "sendContact":
					jids = job.targets.split(",")
					for jid in jids:
						self.sendVCard(jid)
					job.sent = True
				elif job.method == "sendAudio":
					asset = self._getAsset(job.args)
					jids = job.targets.split(",")
					for jid in jids:
						self.sendAudio(jid + "@s.whatsapp.net", asset)
					job.sent = True
				elif job.method == "broadcast_Video":
					args = job.args.encode('utf8').split(",")
					asset = self._getAsset(job.args)
					jids = job.targets.split(",")
					for jid in jids:
						self.sendVideo(jid + "@s.whatsapp.net", asset)
						time.sleep(1)
					job.sent = True
				elif job.method == "broadcast_Group_Image":
					asset = self._getAsset(job.args)
					self.sendImage(job.targets, asset)
					job.sent = True
				elif job.method == "broadcast_Group_Video":
					asset = self._getAsset(job.args)
					self.sendVideo(job.targets, asset)
					job.sent = True
				elif job.method == "typing_send":
					job.sent = True

		
		self.s.commit()	

	def _onSchedule(self,scheduled_time):
		return (scheduled_time is None or datetime.now() > self.utc_to_local(scheduled_time))

	def _getAsset(self, args):
		args = args.encode('utf8').split(",")
		asset_id = args[0]
		return self.s.query(Asset).get(asset_id)

	def onReceiptMessageDelivered(self, jid, messageId):
		logging.info("Delivered %s" %messageId)
		logging.info("From %s" %jid)
		# self.s.query(Job).filter_by(sent=False).all()

		session = self.Session()
		job = session.query(Job).filter_by(sent=True, whatsapp_message_id=messageId).scalar()
		if job is not None:
			job.received = True
			session.commit()

			m = session.query(Message).get(job.message_id)
			logging.info("Looking for message with id %s" %job.message_id)
			if m is not None:
				m.received = True
				self.pubnub.publish({
					'channel' : self.pubnub_channel,
					'message' : {
						'type' : 'receipt',
						'message_id' : m.id
					}
				})
				session.commit()



	def onReceiptMessageSent(self, jid, messageId):
		logging.info("Sent %s" %messageId)
		logging.info("To %s" %jid)

	def onPresenceAvailable(self, jid):
		logging.info("JID available %s" %jid)

	def onPresenceUnavailable(self, jid):
		logging.info("JID unavilable %s" %jid)


	def onUploadRequestDuplicate(self,_hash, url):
		logging.info("Upload duplicate")
		logging.info("The url is %s" %url)
		logging.info("The hash is %s" %_hash)	

		asset = self.s.query(Asset).filter_by(asset_hash=_hash).first()
		logging.info("Asset id %s" %asset.mms_url)
		asset.mms_url = url
		self.s.commit()

		put_url = self.url + "/assets/%s" %asset.id
		headers = {'Content-type': 'application/json', 'Accept': 'application/json'}
		data = { "asset" : { "mms_url": url } }

		r = requests.patch(put_url, data=json.dumps(data), headers=headers)		

	def utc_to_local(self,utc_dt):
		# get integer timestamp to avoid precision lost
		timestamp = calendar.timegm(utc_dt.timetuple())
		local_dt = datetime.fromtimestamp(timestamp)
		assert utc_dt.resolution >= timedelta(microseconds=1)
		return local_dt.replace(microsecond=utc_dt.microsecond)

	def onUploadRequestSuccess(self, _hash, url, removeFrom):
		logging.info("Upload Request success")
		logging.info("The url is %s" %url)
		logging.info("The hash is %s" %_hash)
		asset = self.s.query(Asset).filter_by(asset_hash=_hash).first()
		asset.mms_url = url
		self.s.commit()

		path = self.getImageFile(asset)

		logging.info("To upload %s" %path)
		logging.info("To %s" %self.username)

		MU = MediaUploader(self.username + "@s.whatsapp.net", self.username + "@s.whatsapp.net", self.onUploadSucccess, self.onUploadError, self.onUploadProgress)
		MU.upload(path, url, asset.id)

	def onUploadSucccess(self, url, _id):
		logging.info("Upload success!")
		logging.info("Url %s" %url)
		if _id is not None:
			asset = self.s.query(Asset).get(_id)
			asset.mms_url = url
			self.s.commit()
		

	def onUploadError(self):
		logging.info("Error with upload")

	def onUploadProgress(self, progress):
		logging.info("Upload Progress")

	def requestMediaUrl(self, url, asset, preview):
		logging.info("Requesting Url: %s" %url)	
		mtype = asset.asset_type.lower()
		sha1 = hashlib.sha256()

		if not url.startswith("http"):
			url = os.environ['URL'] + url

		if preview is not None and not preview.startswith("http"):
			preview = os.environ['URL'] + preview
		
		file_name = self.getImageFile(asset)
		fp = open(file_name,'wb')
		fp.write(requests.get(url).content)
		fp.close()

		if asset.asset_type != "Audio":
			tb_path = self.getImageThumbnailFile(asset)
			tb = open(tb_path, 'wb')
			tb.write(requests.get(preview).content)
			tb.close()


		fp = open(file_name, 'rb')
		try:
			sha1.update(fp.read())
			hsh = base64.b64encode(sha1.digest())

			asset.asset_hash = hsh
			self.s.commit()

			self.methodsInterface.call("media_requestUpload", (hsh, mtype, os.path.getsize(file_name)))
		finally:
			fp.close()  

	def getImageFile(self, asset):
		if asset.asset_type == "Image":
			path = "_%s"%asset.id + asset.file_file_name
			file_name = "tmp/%s" %path
			return file_name
		elif asset.asset_type == "Video":
			path = "_%s"%asset.id + asset.video_file_name
			file_name = "tmp/%s" %path
			return file_name
		elif asset.asset_type == "Audio":
			path = "_%s"%asset.id + asset.audio_file_name
			file_name = "tmp/%s" %path
			return file_name

	def getImageThumbnailFile(self, asset):
		if asset.asset_type == "Image":
			path = "_%s"%asset.id + "_thumb_" + asset.file_file_name
			file_name = "tmp/%s" %path
			return file_name		
		else:
			path = "_%s"%asset.id + "_thumb_" + asset.video_file_name
			file_name = "tmp/%s" %path
			return file_name	

	def sendVideo(self, target, asset):
		f = open(self.getImageThumbnailFile(asset), 'r')
		stream = base64.b64encode(f.read())
		f.close()
		self.methodsInterface.call("message_videoSend",(target,asset.mms_url,"Video", str(os.path.getsize(self.getImageThumbnailFile(asset))), stream))


	def sendVCard(self, target):
		card = "BEGIN:VCARD\r\n"
		card += "VERSION:3.0\r\n"
		card += "FN:%s\r\n" % os.environ['ACCOUNT_NAME']
		card += "TEL;type=CELL,voice:+%s\r\n" % os.environ['TEL_NUMBER']
		card += "PHOTO;"

		f = open(os.environ['LOGO_PIC'], 'rb')
		hsh = base64.b64encode(f.read())

		card += "BASE64:"
		card += hsh
		
		card += "\r\n"
		card += "END:VCARD\r\n"

		logging.info("data %s" %card)
		self.methodsInterface.call("message_vcardSend", (target, card, os.environ['ACCOUNT_NAME']))


	def sendAudio(self, target, asset):
		logging.info("Sending %s" %asset.mms_url)
		logging.info("To %s" %target)
		logging.info("Name %s" %asset.name)
		logging.info("Size %s" %asset.audio_file_size)
		self.methodsInterface.call("message_audioSend", (target, asset.mms_url, asset.name, str(asset.audio_file_size)))

	def sendImage(self, target, asset):
		f = open(self.getImageThumbnailFile(asset), 'r')
		stream = base64.b64encode(f.read())
		f.close()    	
		logging.info("Target %s" %target)
		logging.info("URL %s" %asset.mms_url)
		logging.info("URL %s" %asset.asset_hash)
		self.methodsInterface.call("message_imageSend",(target,asset.mms_url,"Image", str(os.path.getsize(self.getImageThumbnailFile(asset))), stream))


	def sendMessage(self, target, text):
		logging.info("Message %s" %text)
		jid = target
		logging.info("To %s" %jid)
		rst = self.methodsInterface.call("message_send", (jid, text))	
		return rst

	def onGroupSubjectReceived(self,messageId,jid,author,subject,timestamp,receiptRequested):
		logging.info("Group subject received")
		if receiptRequested and self.sendReceipts:
			self.methodsInterface.call("message_ack", (jid, messageId))

		put_url = self.url  + "/groups"
		headers = {'Content-type': 'application/json', 'Accept': 'application/json'}
		data = { "name" : subject, "group_type" : "External", "jid" : jid }
		r = requests.post(put_url, data=json.dumps(data), headers=headers)
		logging.info("Updated the group")

	def onGroupAddParticipantsSuccess(self, groupJid, jid):
		logging.info("Added participant %s" %jid)
		# check the profile pic
		self.checkProfilePic(jid[0])

	def onNotificationRemovedFromGroup(self, groupJid,jid):
		logging.info("You were removed from the group %s" %groupJid)

		put_url = self.url  + "/groups/disable_group"
		headers = {'Content-type': 'application/json', 'Accept': 'application/json'}
		data = { "groupJid" : groupJid }
		r = requests.post(put_url, data=json.dumps(data), headers=headers)
		logging.info("Updated the group")


	def onGotGroupParticipants(self, groupJid, jids):
		logging.info("Got group participants")

		put_url = self.url  + "/groups/update_membership"
		headers = {'Content-type': 'application/json', 'Accept': 'application/json'}
		data = { "groupJid" : groupJid, "jids" : jids }
		r = requests.post(put_url, data=json.dumps(data), headers=headers)

	def onGroupCreateSuccess(self, groupJid):
		logging.info("Created with id %s" %groupJid)
		self.methodsInterface.call("group_getInfo", (groupJid,))

	def onGroupGotInfo(self,jid,owner,subject,subjectOwner,subjectTimestamp,creationTimestamp):
		logging.info("Group info %s - %s" %(jid, subject))
		
		put_url = self.url + "/update_group"
		headers = {'Content-type': 'application/json', 'Accept': 'application/json'}
		data = { "name" : subject, "jid" : jid }

		r = requests.post(put_url, data=json.dumps(data), headers=headers)
		logging.info("Updated the group")

	def onGroupCreateFail(self, errorCode):
		logging.info("Error creating a group %s" %errorCode)

	def onSetStatusSuccess(self,jid,messageId):
		logging.info("Set the profile message for %s - %s" %(jid, messageId))

	def onAuthSuccess(self, username):
		logging.info("We are authenticated")
		self.methodsInterface.call("ready")
		self.setStatus(1, "Authenticated")

		# logo_url = os.environ['LOGO_PIC']
		# status = os.environ['STATUS_MSG']

		# logging.info("The pic is %s" %logo_url)
		# logging.info("Status MSG %s" %status)

		# self.methodsInterface.call("profile_setPicture", (logo_url,))
		# self.methodsInterface.call("profile_setStatus", (status,))
        

	def setStatus(self, status, message="Status message"):
		logging.info("Setting status %s" %status)
		post_url = self.url + "/status"
		data = { "status" : status, "message" : message }
		r = requests.post(post_url, data=json.dumps(data), headers=self.post_headers)

	def onAuthFailed(self, username, err):
		logging.info('Authentication failed')
		
	def onDisconnected(self, reason):
		logging.info('Disconnected')
		self.setStatus(0, "Got disconnected")
		# self.done = True
		logging.info('About to log in again with %s and %s' %(self.username, self.password))
		self.login(self.username, self.password)

	def onGotProfilePicture(self, jid, imageId, filePath):
		logging.info('Got profile picture')
		url = self.url + "/contacts/" + jid.split("@")[0] + "/upload"
		files = {'file': open(filePath, 'rb')}
		r = requests.post(url, files=files)

	def checkProfilePic(self, jid):
		pull_pic = os.environ['PULL_STATUS_PIC']
		if pull_pic == "true":
			phone_number = jid.split("@")[0]
			get_url = self.url + "/profile?phone_number=" + phone_number
			headers = {'Content-type': 'application/json', 'Accept': 'application/json'}
			r = requests.get(get_url, headers=headers)
			response = r.json()
			
			if response['profile_url'] == '/missing.jpg':		
				self.methodsInterface.call("contact_getProfilePicture", (jid,))	

	def onGroupMessageReceived(self, messageId, jid, author, content, timestamp, wantsReceipt, pushName):
		logging.info('Received a message on the group %s' %content)
		logging.info('JID %s - %s - %s' %(jid, pushName, author))

		if wantsReceipt and self.sendReceipts:
			self.methodsInterface.call("message_ack", (jid, messageId))

		headers = {'Content-type': 'application/json', 'Accept': 'application/json' }
		data = { "message" : { "text" : content, "group_jid" : jid, "message_type" : "Text", "whatsapp_message_id" : messageId, "name" : pushName, "jid" : author }}

		post_url = self.url + "/receive_broadcast"
		r = requests.post(post_url, data=json.dumps(data), headers=headers)

		self.checkProfilePic(author)

		channel = os.environ['PUB_CHANNEL'] + "_%s" %self.username
		self.pubnub.publish({
			'channel' : channel,
			'message' : {
				'type' : 'text',
				'phone_number' : jid,
				'text' : content,
				'name' : pushName
			}
		})

	def onMessageReceived(self, messageId, jid, messageContent, timestamp, wantsReceipt, pushName, isBroadCast):
		logging.info('Message Received %s' %messageContent)
		phone_number = jid.split("@")[0]
		headers = {'Content-type': 'application/json', 'Accept': 'application/json'}
		data = { "message" : { "text" : messageContent, "phone_number" : phone_number, "message_type" : "Text", "whatsapp_message_id" : messageId, "name" : pushName  }}
		post_url = self.url + "/messages"
		r = requests.post(post_url, data=json.dumps(data), headers=headers)

		if wantsReceipt and self.sendReceipts:
			self.methodsInterface.call("message_ack", (jid, messageId))

		channel = os.environ['PUB_CHANNEL'] + "_%s" %self.username
		self.pubnub.publish({
			'channel' : channel,
			'message' : {
				'type' : 'text',
				'phone_number' : phone_number,
				'text' : messageContent,
				'name' : pushName
			}
		})
		
		self.checkProfilePic(jid)	

	def onImageReceived(self, messageId, jid, preview, url, size, wantsReceipt, isBroadCast):	
		logging.info('Image Received')	
		phone_number = jid.split("@")[0]

		# print preview
		post_url = self.url + "/upload"
		headers = {'Content-type': 'application/json', 'Accept': 'application/json'}
		data = { "message" : { 'url' : url, 'message_type' : 'Image' , 'phone_number' : phone_number, "whatsapp_message_id" : messageId, 'name' : '' } }
		r = requests.post(post_url, data=json.dumps(data), headers=headers)

		if wantsReceipt and self.sendReceipts:
			self.methodsInterface.call("message_ack", (jid, messageId))

		channel = os.environ['PUB_CHANNEL'] + "_%s" %self.username
		self.pubnub.publish({
			'channel' : channel,
			'message' : {
				'type' : 'image',
				'phone_number' : phone_number,
				'url' : url,
				'name' : ''
			}
		})

		self.checkProfilePic(jid)

	
	def onVCardReceived(self, messageId, jid, name, data, wantsReceipt, isBroadcast):
		if wantsReceipt and self.sendReceipts:
			self.methodsInterface.call("message_ack", (jid, messageId))

	def onAudioReceived(self, messageId, jid, url, size, wantsReceipt, isBroadcast):
		logging.info("Audio received %s" %messageId)
		logging.info("url: %s" %url)
		phone_number = jid.split("@")[0]

		post_url = self.url + "/upload"
		headers = {'Content-type': 'application/json', 'Accept': 'application/json' }
		data = { "message" : { 'url' : url,  'message_type': 'Audio', 'phone_number' : phone_number, "whatsapp_message_id" : messageId, 'name' : '' } }

		r = requests.post(post_url, data=json.dumps(data), headers=headers)
		
		if wantsReceipt and self.sendReceipts:
			self.methodsInterface.call("message_ack", (jid, messageId))

	def onVideoReceived(self, messageId, jid, mediaPreview, mediaUrl, mediaSize, wantsReceipt, isBroadcast):
		logging.info("Video Received %s" %messageId)
		logging.info("From %s" %jid)
		logging.info("url: %s" %mediaUrl)

		post_url = self.url + "/upload"
		phone_number = jid.split("@")[0]
		headers = {'Content-type': 'application/json', 'Accept': 'application/json'}
		data = { "message" : { 'url' : mediaUrl, 'message_type' : 'Video', 'phone_number' : phone_number, "whatsapp_message_id" : messageId, 'name' : '' } }

		# Send a receipt regardless of whether it was a successful upload
		if wantsReceipt and self.sendReceipts:
			self.methodsInterface.call("message_ack", (jid, messageId))
		r = requests.post(post_url, data=json.dumps(data), headers=headers)


	def onGotProfilePicture(self, jid, imageId, filePath):
		logging.info('Profile picture received')
		url = self.url + "/contacts/" + jid.split("@")[0] + "/upload"
		files = {'file': open(filePath, 'rb')}
		r = requests.post(url, files=files)


database_url = os.environ['SQLALCHEMY_DATABASE_URI']
server = Server(database_url,True, True)
login = os.environ['TEL_NUMBER']
password = os.environ['PASS']
password = base64.b64decode(bytes(password.encode('utf-8')))
server.login(login, password)
