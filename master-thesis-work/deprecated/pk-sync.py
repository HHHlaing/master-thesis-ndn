#!/usr/bin/python
import pksyncbuf_pb2
import sys
import logging
import time
import random
import select
from pyndn import Name
from pyndn import Interest
from pyndn import Data
from pyndn import Face
from pyndn.security import KeyType
from pyndn.security import KeyChain
from pyndn.security.identity import IdentityManager
from pyndn.security.identity import MemoryIdentityStorage
from pyndn.security.identity import MemoryPrivateKeyStorage
from pyndn.security.policy import NoVerifyPolicyManager
from pyndn.util import Blob
from pyndn.sync import ChronoSync2013

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog.events import FileModifiedEvent

class PublicKeySync(object):
    #pkList == chatRomm
    def __init__(self, screenName, pkListName, hubPrefix, face, keyChain, certificateName):
        """
        PublicKeySync:
            To be written (TBW)

        """
        self.screenName = screenName
        self.pkListName = pkListName

        # ChronoSync2013: The Face for calling registerPrefix and expressInterest. 
        # The Face object must remain valid for the life of this ChronoSync2013 object.
        self.face = face
        self.keyChain = keyChain
        self.certificateName = certificateName

        self.syncDataCache = [] # of CachedSyncData
        self.roster = [] # of str (list of all nodes that are subscribing)
        self.maxDataCacheLength = 100
        self.isRecoverySyncState = True
        self.syncLifetime = 15000.0 # milliseconds
        
        #Watch the public key for changes
        watchPath = ""
        self.watchKey(watchPath)

        # This should only be called once, so get the random string here.
        self.pkListPrefix = Name(hubPrefix).append(self.pkListName).append(self.getRandomString())

        # ChronoSync2013: The session number used with the applicationDataPrefix in sync state messages.
        session = int(round(self.getNowMilliseconds() / 1000.0)) 

        self.userName = self.screenName + str(session)
        
        broadcastPrefix = Name("/ndn/broadcast/PublicKeySync-0.1").append(self.pkListName)
        self.sync = ChronoSync2013(
           self.sendInterest,             #onReceivedSyncState         (function object)
           self.initial,                #onInitialized                 (function object)
           self.pkListPrefix,            #applicationDataPrefix        (Name)
           broadcastPrefix,             #applicationBroadcastPrefix    (Name)
           session,                        #sessionNo                    (int)
           self.face,                    #face                         (Face)
           self.keyChain,                #KeyChain                     (KeyChain)
           self.certificateName,        #certificateName            (Name)
           self.syncLifetime,            #syncLifetime                (float)
           onRegisterFailed)            #onRegisterFailed             (function object)

        face.registerPrefix(self.pkListPrefix, self.onInterest, onRegisterFailed)

    def sendUpdatedPublicKey(self, data):
        """
        PublicKeySync:
            When a key pair is edited, i.e. renewed, the application will publish a new sequence number in ChronoSync2013

        """
        # When the application wants to publish data, it calls ChronoSync2013 method publishNextSequenceNo()

        #Subscribe to public key sync "room" if not subscribing already
        if len(self.syncDataCache) == 0:
            self.syncDataCacheAppend(pksyncbuf_pb2.PublicKeySync.SUBSCRIBE, "xxx")

        #TODO: check wether the new public key is new.
        self.sync.publishNextSequenceNo()
        self.syncDataCacheAppend(pksyncbuf_pb2.PublicKeySync.PK_UPDATE, data)
        print "New public key published!"

    def unsubscribe(self):
        """
        PublicKeySync:
            Send the unsubscribe message and unsubscribe the public key.
        """
        self.sync.publishNextSequenceNo()
        self.syncDataCacheAppend(pksyncbuf_pb2.PublicKeySync.UNSUBSCRIBE, "xxx")

    # onInitialized
    def initial(self):
        """
        PublicKeySync:
            To be written (TBW)

        ChronoSync2013 docs: 
        onInitialized: 
            This calls onInitialized() when the first sync data is received 
            (or the interest times out because there are no other publishers yet).
        """
        timeout = Interest(Name("/local/timeout"))
        timeout.setInterestLifetimeMilliseconds(60000)
        self.face.expressInterest(timeout, self.dummyOnData, self.onTimeout)

        try:
            self.roster.index(self.userName)
        except ValueError:
            self.roster.append(self.userName)
            print("Member: " + self.screenName)
            print(self.screenName + ": Subscribe")
            self.syncDataCacheAppend(pksyncbuf_pb2.PublicKeySync.SUBSCRIBE, "xxx")

    # onReceivedSyncState
    def sendInterest(self, syncStates, isRecovery):
        """
        PublicKeySync:
            To be written (TBW)

        ChronoSync2013 docs:
        onReceivedSyncState: 
            When ChronoSync receives a sync state message, this calls onReceivedSyncState(syncStates, isRecovery) 
            where syncStates is the list of SyncState messages and isRecovery is true if this is the initial list of SyncState 
            messages or from a recovery interest. (For example, if isRecovery is true, a chat application would not 
            want to re-display all the associated chat messages.) The callback should send interests to fetch the application 
            data for the sequence numbers in the sync state.
        """
        self.isRecoverySyncState = isRecovery
        dump("onReceivedSyncState in recovery: ", self.isRecoverySyncState)

        sendList = []       # of str
        sessionNoList = []  # of int
        sequenceNoList = [] # of int
        # Loops through the syncStates
        # ChronoSync2013: A SyncState holds the values of a sync state message which is passed to the 
        #     onReceivedSyncState callback which was given to the ChronoSync2013 constructor.
        for j in range(len(syncStates)):
            syncState = syncStates[j]

            # ChronoSync2013: Get the application data prefix for this sync state message.
            nameComponents = Name(syncState.getDataPrefix())

            #TODO not used..
            tempName = nameComponents.get(-1).toEscapedString()
            # tempName is the random string 
            # ChronoSync2013: Get the sequence number for this sync state message.
            sequenceNo = syncState.getSequenceNo()
            # ChronoSync2013: Get the session number associated with the application data prefix for this sync state message.
            sessionNo = syncState.getSessionNo()

            #Loop through sendList for not adding duplcates
            index = -1
            for k in range(len(sendList)):
                if sendList[k] == syncState.getDataPrefix():
                    index = k
                    break
            if index != -1:
                sessionNoList[index] = sessionNo
                sequenceNoList[index] = sequenceNo
            else:
                #append to sendList for sending out interest
                sendList.append(syncState.getDataPrefix())
                sessionNoList.append(sessionNo)
                sequenceNoList.append(sequenceNo)

        # Loop through all syncStates and send an interest for all. 
        for i in range(len(sendList)):
            uri = (sendList[i] + "/" + str(sessionNoList[i]) + "/" + str(sequenceNoList[i]))
            interestName = Name(uri)
            dump("Sync - sending interest: ", interestName.toUri())

            interest = Interest(interestName)
            interest.setInterestLifetimeMilliseconds(self.syncLifetime)
            self.face.expressInterest(interest, self.onData, self.onTimeout)

    def onInterest(self, prefix, interest, transport, registeredPrefixId):
        """
        PublicKeySync:
            To be written (TBW)

        """
        dump("Got interest packet with name", interest.getName().toUri())

        content = pksyncbuf_pb2.PublicKeySync()
        sequenceNo = int(
            interest.getName().get(self.pkListPrefix.size() + 1).toEscapedString())
        gotContent = False
        
        #loop through all cached data and find out if you have some new content to respond with
        for i in range(len(self.syncDataCache) - 1, -1, -1):
            data = self.syncDataCache[i]
            if data.sequenceNo == sequenceNo:
                if data.dataType != pksyncbuf_pb2.PublicKeySync.PK_UPDATE:
                    # Use setattr because "from" is a reserved keyword.
                    setattr(content, "from", self.screenName)
                    content.to             = self.pkListName
                    content.dataType     = data.dataType
                    content.timestamp     = int(round(data.time / 1000.0))
                else:
                    setattr(content, "from", self.screenName)
                    content.to             = self.pkListName
                    content.dataType     = data.dataType
                    content.data         = data.data
                    content.timestamp     = int(round(data.time / 1000.0))
                gotContent = True
                break
        
        if gotContent:
            print "new content!"
            #Serialize the pklistbuf
            array = content.SerializeToString()
            #Initialize the data with Name
            data = Data(interest.getName())
            #Set content for the data --> the serialized content to bytes
            data.setContent(Blob(array))
            #Add sign the data
            self.keyChain.sign(data, self.certificateName)
            try:
                transport.send(data.wireEncode().toBuffer())
            except Exception as ex:
                logging.getLogger(__name__).error(
                "Error in transport.send: %s", str(ex))
                return
        

    def onData(self, interest, data):
        """
        PublicKeySync:
            To be written (TBW)

        """
        # TODO: Verify packet
        self.keyChain.verifyData(data, self.onVerified, self.onVerifyFailed)

        dump("Got data packet with name", data.getName().toUri())
        content = pksyncbuf_pb2.PublicKeySync()
        content.ParseFromString(data.getContent().toRawStr())
        print("Type: " + str(content.dataType) + ", data: "+content.data)

        if self.getNowMilliseconds() - content.timestamp * 1000.0 < 120000.0:
            # Use getattr because "from" is a reserved keyword.
            name = getattr(content, "from")
            prefix = data.getName().getPrefix(-2).toUri()
            sessionNo = int(data.getName().get(-2).toEscapedString())
            sequenceNo = int(data.getName().get(-1).toEscapedString())
            nameAndSession = name + str(sessionNo)


            l = 0
            # Update roster.
            while l < len(self.roster):
                entry = self.roster[l]
                tempName = entry[0:len(entry) - 10]
                tempSessionNo = int(entry[len(entry) - 10:])
                if (name != tempName and
                    content.dataType != pksyncbuf_pb2.PublicKeySync.UNSUBSCRIBE):
                    l += 1
                else:
                    if name == tempName and sessionNo > tempSessionNo:
                        self.roster[l] = nameAndSession
                    break

            if l == len(self.roster):
                self.roster.append(nameAndSession)
                print(name + ": Subscribe")


            # Use getattr because "from" is a reserved keyword.
            if (content.dataType == pksyncbuf_pb2.PublicKeySync.PK_UPDATE and
                not self.isRecoverySyncState and getattr(content, "from") != self.screenName):
                print(getattr(content, "from") + ": " + content.data)
            elif content.dataType == pksyncbuf_pb2.PublicKeySync.UNSUBSCRIBE:
                # leave message
                try:
                    n = self.roster.index(nameAndSession)
                    if name != self.screenName:
                        self.roster.pop(n)
                        print(name + ": Unsubscribe")
                except ValueError:
                    pass

    def onVerified(self, data):
        #TODO
        print("Data packet verified")

    def onVerifyFailed(self, data):
        #TODO
        print("Data packet failed verification")

    def heartbeat(self, interest):
        """
        This repeatedly calls itself after a timeout to send a heartbeat message
        (pksync message type HELLO). This method has an "interest" argument
        because we use it as the onTimeout for Face.expressInterest.
        """
        if len(self.syncDataCache) == 0:
            self.syncDataCacheAppend(pksyncbuf_pb2.PublicKeySync.SUBSCRIBE, "xxx")

        self.sync.publishNextSequenceNo()
        self.syncDataCacheAppend(pksyncbuf_pb2.PublicKeySync.HELLO, "xxx")

        # Call again.
        # TODO: Are we sure using a "/local/timeout" interest is the best future call
        # approach?
        timeout = Interest(Name("/local/timeout"))
        timeout.setInterestLifetimeMilliseconds(60000)
        self.face.expressInterest(timeout, self.dummyOnData, self.heartbeat)

    def onTimeout(self, interest):
        """
        PublicKeySync:
            To be written (TBW)
        """
        dump("Time out for interest", interest.getName().toUri())

    def syncDataCacheAppend(self, dataType, data):
        """
        PublicKeySync:
            To be written (TBW)

        ChronoChat:
            Append a new CachedMessage to messageCache_, using given messageType and
            message, the sequence number from _sync.getSequenceNo() and the current
            time. Also remove elements from the front of the cache as needed to keep
            the size to _maxMessageCacheLength.
        """
        cachedData = self.CachedData(self.sync.getSequenceNo(), dataType, data, self.getNowMilliseconds())

        self.syncDataCache.append(cachedData)

        while len(self.syncDataCache) > self.maxDataCacheLength:
            self.syncDataCache.pop(0)

    @staticmethod
    def getNowMilliseconds():
        """
        Get the current time in milliseconds.
        
        :return: The current time in milliseconds since 1/1/1970, including fractions of a millisecond.
        :rtype: float
        """
        return time.time() * 1000.0

    @staticmethod
    def getRandomString():
        """
        Generate a random name for ChronoSync.
        """
        #TODO: better seed
        seed = "qwertyuiopasdfghjklzxcvbnmQWERTYUIOPASDFGHJKLZXCVBNM0123456789"
        result = ""
        for i in range(10):
            # Using % means the distribution isn't uniform, but that's OK.
            position = random.randrange(256) % len(seed)
            result += seed[position]

        return result


    @staticmethod
    def dummyOnData(interest, data):
        """
        This is a do-nothing onData for using expressInterest for timeouts.
        This should never be called.
        """
        pass

    class CachedData(object):
        def __init__(self, sequenceNo, dataType, data, time):
            self.sequenceNo = sequenceNo
            self.dataType = dataType
            self.data = data
            self.time = time

    # TODO : implement a subclass of watchdog.events.FileSystemEventHandler to be event_handler
    def watchKey(self, path):
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
        path = path if len(path) > 0 else './public_keys/'
        self.event_handler = self.EventHandler(self)
        self.observer = Observer()
        self.observer.schedule(self.event_handler, path, recursive=True)
        self.observer.start()

    class EventHandler(FileSystemEventHandler):
        def __init__(self, pkSync):
            self.pkSync = pkSync

        def on_any_event(self, event):
            """Catch-all event handler.

            :param event:
                The event object representing the file system event.
            :type event:
                :class:`FileSystemEvent`
            """

        def on_moved(self, event):
            """Called when a file or a directory is moved or renamed.

            :param event:
                Event representing file/directory movement.
            :type event:
            :class:`DirMovedEvent` or :class:`FileMovedEvent`
            """

        def on_created(self, event):
            """Called when a file or directory is created.

            :param event:
                Event representing file/directory creation.
            :type event:
                :class:`DirCreatedEvent` or :class:`FileCreatedEvent`
            """

        def on_deleted(self, event):
            """Called when a file or directory is deleted.

            :param event:
                Event representing file/directory deletion.
            :type event:
                :class:`DirDeletedEvent` or :class:`FileDeletedEvent`
            """

        def on_modified(self, event):
            """Called when a file or directory is modified.

            :param event:
                Event representing file/directory modification.
            :type event:
                :class:`DirModifiedEvent` or :class:`FileModifiedEvent`
            """
            if not event.is_directory:
                print "A file changed:"
                print event.src_path
                keyFile = open(str(event.src_path), 'r')
                key = keyFile.read()
                self.pkSync.sendUpdatedPublicKey(key)
                keyFile.close()


DEFAULT_RSA_PUBLIC_KEY_DER = bytearray([
    0x30, 0x82, 0x01, 0x22, 0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01,
    0x01, 0x05, 0x00, 0x03, 0x82, 0x01, 0x0f, 0x00, 0x30, 0x82, 0x01, 0x0a, 0x02, 0x82, 0x01, 0x01,
    0x00, 0xb8, 0x09, 0xa7, 0x59, 0x82, 0x84, 0xec, 0x4f, 0x06, 0xfa, 0x1c, 0xb2, 0xe1, 0x38, 0x93,
    0x53, 0xbb, 0x7d, 0xd4, 0xac, 0x88, 0x1a, 0xf8, 0x25, 0x11, 0xe4, 0xfa, 0x1d, 0x61, 0x24, 0x5b,
    0x82, 0xca, 0xcd, 0x72, 0xce, 0xdb, 0x66, 0xb5, 0x8d, 0x54, 0xbd, 0xfb, 0x23, 0xfd, 0xe8, 0x8e,
    0xaf, 0xa7, 0xb3, 0x79, 0xbe, 0x94, 0xb5, 0xb7, 0xba, 0x17, 0xb6, 0x05, 0xae, 0xce, 0x43, 0xbe,
    0x3b, 0xce, 0x6e, 0xea, 0x07, 0xdb, 0xbf, 0x0a, 0x7e, 0xeb, 0xbc, 0xc9, 0x7b, 0x62, 0x3c, 0xf5,
    0xe1, 0xce, 0xe1, 0xd9, 0x8d, 0x9c, 0xfe, 0x1f, 0xc7, 0xf8, 0xfb, 0x59, 0xc0, 0x94, 0x0b, 0x2c,
    0xd9, 0x7d, 0xbc, 0x96, 0xeb, 0xb8, 0x79, 0x22, 0x8a, 0x2e, 0xa0, 0x12, 0x1d, 0x42, 0x07, 0xb6,
    0x5d, 0xdb, 0xe1, 0xf6, 0xb1, 0x5d, 0x7b, 0x1f, 0x54, 0x52, 0x1c, 0xa3, 0x11, 0x9b, 0xf9, 0xeb,
    0xbe, 0xb3, 0x95, 0xca, 0xa5, 0x87, 0x3f, 0x31, 0x18, 0x1a, 0xc9, 0x99, 0x01, 0xec, 0xaa, 0x90,
    0xfd, 0x8a, 0x36, 0x35, 0x5e, 0x12, 0x81, 0xbe, 0x84, 0x88, 0xa1, 0x0d, 0x19, 0x2a, 0x4a, 0x66,
    0xc1, 0x59, 0x3c, 0x41, 0x83, 0x3d, 0x3d, 0xb8, 0xd4, 0xab, 0x34, 0x90, 0x06, 0x3e, 0x1a, 0x61,
    0x74, 0xbe, 0x04, 0xf5, 0x7a, 0x69, 0x1b, 0x9d, 0x56, 0xfc, 0x83, 0xb7, 0x60, 0xc1, 0x5e, 0x9d,
    0x85, 0x34, 0xfd, 0x02, 0x1a, 0xba, 0x2c, 0x09, 0x72, 0xa7, 0x4a, 0x5e, 0x18, 0xbf, 0xc0, 0x58,
    0xa7, 0x49, 0x34, 0x46, 0x61, 0x59, 0x0e, 0xe2, 0x6e, 0x9e, 0xd2, 0xdb, 0xfd, 0x72, 0x2f, 0x3c,
    0x47, 0xcc, 0x5f, 0x99, 0x62, 0xee, 0x0d, 0xf3, 0x1f, 0x30, 0x25, 0x20, 0x92, 0x15, 0x4b, 0x04,
    0xfe, 0x15, 0x19, 0x1d, 0xdc, 0x7e, 0x5c, 0x10, 0x21, 0x52, 0x21, 0x91, 0x54, 0x60, 0x8b, 0x92,
    0x41, 0x02, 0x03, 0x01, 0x00, 0x01
  ])

# Pycrypto uses the same private key format as openssl.
DEFAULT_RSA_PRIVATE_KEY_DER = bytearray([
    0x30, 0x82, 0x04, 0xa5, 0x02, 0x01, 0x00, 0x02, 0x82, 0x01, 0x01, 0x00, 0xb8, 0x09, 0xa7, 0x59,
    0x82, 0x84, 0xec, 0x4f, 0x06, 0xfa, 0x1c, 0xb2, 0xe1, 0x38, 0x93, 0x53, 0xbb, 0x7d, 0xd4, 0xac,
    0x88, 0x1a, 0xf8, 0x25, 0x11, 0xe4, 0xfa, 0x1d, 0x61, 0x24, 0x5b, 0x82, 0xca, 0xcd, 0x72, 0xce,
    0xdb, 0x66, 0xb5, 0x8d, 0x54, 0xbd, 0xfb, 0x23, 0xfd, 0xe8, 0x8e, 0xaf, 0xa7, 0xb3, 0x79, 0xbe,
    0x94, 0xb5, 0xb7, 0xba, 0x17, 0xb6, 0x05, 0xae, 0xce, 0x43, 0xbe, 0x3b, 0xce, 0x6e, 0xea, 0x07,
    0xdb, 0xbf, 0x0a, 0x7e, 0xeb, 0xbc, 0xc9, 0x7b, 0x62, 0x3c, 0xf5, 0xe1, 0xce, 0xe1, 0xd9, 0x8d,
    0x9c, 0xfe, 0x1f, 0xc7, 0xf8, 0xfb, 0x59, 0xc0, 0x94, 0x0b, 0x2c, 0xd9, 0x7d, 0xbc, 0x96, 0xeb,
    0xb8, 0x79, 0x22, 0x8a, 0x2e, 0xa0, 0x12, 0x1d, 0x42, 0x07, 0xb6, 0x5d, 0xdb, 0xe1, 0xf6, 0xb1,
    0x5d, 0x7b, 0x1f, 0x54, 0x52, 0x1c, 0xa3, 0x11, 0x9b, 0xf9, 0xeb, 0xbe, 0xb3, 0x95, 0xca, 0xa5,
    0x87, 0x3f, 0x31, 0x18, 0x1a, 0xc9, 0x99, 0x01, 0xec, 0xaa, 0x90, 0xfd, 0x8a, 0x36, 0x35, 0x5e,
    0x12, 0x81, 0xbe, 0x84, 0x88, 0xa1, 0x0d, 0x19, 0x2a, 0x4a, 0x66, 0xc1, 0x59, 0x3c, 0x41, 0x83,
    0x3d, 0x3d, 0xb8, 0xd4, 0xab, 0x34, 0x90, 0x06, 0x3e, 0x1a, 0x61, 0x74, 0xbe, 0x04, 0xf5, 0x7a,
    0x69, 0x1b, 0x9d, 0x56, 0xfc, 0x83, 0xb7, 0x60, 0xc1, 0x5e, 0x9d, 0x85, 0x34, 0xfd, 0x02, 0x1a,
    0xba, 0x2c, 0x09, 0x72, 0xa7, 0x4a, 0x5e, 0x18, 0xbf, 0xc0, 0x58, 0xa7, 0x49, 0x34, 0x46, 0x61,
    0x59, 0x0e, 0xe2, 0x6e, 0x9e, 0xd2, 0xdb, 0xfd, 0x72, 0x2f, 0x3c, 0x47, 0xcc, 0x5f, 0x99, 0x62,
    0xee, 0x0d, 0xf3, 0x1f, 0x30, 0x25, 0x20, 0x92, 0x15, 0x4b, 0x04, 0xfe, 0x15, 0x19, 0x1d, 0xdc,
    0x7e, 0x5c, 0x10, 0x21, 0x52, 0x21, 0x91, 0x54, 0x60, 0x8b, 0x92, 0x41, 0x02, 0x03, 0x01, 0x00,
    0x01, 0x02, 0x82, 0x01, 0x01, 0x00, 0x8a, 0x05, 0xfb, 0x73, 0x7f, 0x16, 0xaf, 0x9f, 0xa9, 0x4c,
    0xe5, 0x3f, 0x26, 0xf8, 0x66, 0x4d, 0xd2, 0xfc, 0xd1, 0x06, 0xc0, 0x60, 0xf1, 0x9f, 0xe3, 0xa6,
    0xc6, 0x0a, 0x48, 0xb3, 0x9a, 0xca, 0x21, 0xcd, 0x29, 0x80, 0x88, 0x3d, 0xa4, 0x85, 0xa5, 0x7b,
    0x82, 0x21, 0x81, 0x28, 0xeb, 0xf2, 0x43, 0x24, 0xb0, 0x76, 0xc5, 0x52, 0xef, 0xc2, 0xea, 0x4b,
    0x82, 0x41, 0x92, 0xc2, 0x6d, 0xa6, 0xae, 0xf0, 0xb2, 0x26, 0x48, 0xa1, 0x23, 0x7f, 0x02, 0xcf,
    0xa8, 0x90, 0x17, 0xa2, 0x3e, 0x8a, 0x26, 0xbd, 0x6d, 0x8a, 0xee, 0xa6, 0x0c, 0x31, 0xce, 0xc2,
    0xbb, 0x92, 0x59, 0xb5, 0x73, 0xe2, 0x7d, 0x91, 0x75, 0xe2, 0xbd, 0x8c, 0x63, 0xe2, 0x1c, 0x8b,
    0xc2, 0x6a, 0x1c, 0xfe, 0x69, 0xc0, 0x44, 0xcb, 0x58, 0x57, 0xb7, 0x13, 0x42, 0xf0, 0xdb, 0x50,
    0x4c, 0xe0, 0x45, 0x09, 0x8f, 0xca, 0x45, 0x8a, 0x06, 0xfe, 0x98, 0xd1, 0x22, 0xf5, 0x5a, 0x9a,
    0xdf, 0x89, 0x17, 0xca, 0x20, 0xcc, 0x12, 0xa9, 0x09, 0x3d, 0xd5, 0xf7, 0xe3, 0xeb, 0x08, 0x4a,
    0xc4, 0x12, 0xc0, 0xb9, 0x47, 0x6c, 0x79, 0x50, 0x66, 0xa3, 0xf8, 0xaf, 0x2c, 0xfa, 0xb4, 0x6b,
    0xec, 0x03, 0xad, 0xcb, 0xda, 0x24, 0x0c, 0x52, 0x07, 0x87, 0x88, 0xc0, 0x21, 0xf3, 0x02, 0xe8,
    0x24, 0x44, 0x0f, 0xcd, 0xa0, 0xad, 0x2f, 0x1b, 0x79, 0xab, 0x6b, 0x49, 0x4a, 0xe6, 0x3b, 0xd0,
    0xad, 0xc3, 0x48, 0xb9, 0xf7, 0xf1, 0x34, 0x09, 0xeb, 0x7a, 0xc0, 0xd5, 0x0d, 0x39, 0xd8, 0x45,
    0xce, 0x36, 0x7a, 0xd8, 0xde, 0x3c, 0xb0, 0x21, 0x96, 0x97, 0x8a, 0xff, 0x8b, 0x23, 0x60, 0x4f,
    0xf0, 0x3d, 0xd7, 0x8f, 0xf3, 0x2c, 0xcb, 0x1d, 0x48, 0x3f, 0x86, 0xc4, 0xa9, 0x00, 0xf2, 0x23,
    0x2d, 0x72, 0x4d, 0x66, 0xa5, 0x01, 0x02, 0x81, 0x81, 0x00, 0xdc, 0x4f, 0x99, 0x44, 0x0d, 0x7f,
    0x59, 0x46, 0x1e, 0x8f, 0xe7, 0x2d, 0x8d, 0xdd, 0x54, 0xc0, 0xf7, 0xfa, 0x46, 0x0d, 0x9d, 0x35,
    0x03, 0xf1, 0x7c, 0x12, 0xf3, 0x5a, 0x9d, 0x83, 0xcf, 0xdd, 0x37, 0x21, 0x7c, 0xb7, 0xee, 0xc3,
    0x39, 0xd2, 0x75, 0x8f, 0xb2, 0x2d, 0x6f, 0xec, 0xc6, 0x03, 0x55, 0xd7, 0x00, 0x67, 0xd3, 0x9b,
    0xa2, 0x68, 0x50, 0x6f, 0x9e, 0x28, 0xa4, 0x76, 0x39, 0x2b, 0xb2, 0x65, 0xcc, 0x72, 0x82, 0x93,
    0xa0, 0xcf, 0x10, 0x05, 0x6a, 0x75, 0xca, 0x85, 0x35, 0x99, 0xb0, 0xa6, 0xc6, 0xef, 0x4c, 0x4d,
    0x99, 0x7d, 0x2c, 0x38, 0x01, 0x21, 0xb5, 0x31, 0xac, 0x80, 0x54, 0xc4, 0x18, 0x4b, 0xfd, 0xef,
    0xb3, 0x30, 0x22, 0x51, 0x5a, 0xea, 0x7d, 0x9b, 0xb2, 0x9d, 0xcb, 0xba, 0x3f, 0xc0, 0x1a, 0x6b,
    0xcd, 0xb0, 0xe6, 0x2f, 0x04, 0x33, 0xd7, 0x3a, 0x49, 0x71, 0x02, 0x81, 0x81, 0x00, 0xd5, 0xd9,
    0xc9, 0x70, 0x1a, 0x13, 0xb3, 0x39, 0x24, 0x02, 0xee, 0xb0, 0xbb, 0x84, 0x17, 0x12, 0xc6, 0xbd,
    0x65, 0x73, 0xe9, 0x34, 0x5d, 0x43, 0xff, 0xdc, 0xf8, 0x55, 0xaf, 0x2a, 0xb9, 0xe1, 0xfa, 0x71,
    0x65, 0x4e, 0x50, 0x0f, 0xa4, 0x3b, 0xe5, 0x68, 0xf2, 0x49, 0x71, 0xaf, 0x15, 0x88, 0xd7, 0xaf,
    0xc4, 0x9d, 0x94, 0x84, 0x6b, 0x5b, 0x10, 0xd5, 0xc0, 0xaa, 0x0c, 0x13, 0x62, 0x99, 0xc0, 0x8b,
    0xfc, 0x90, 0x0f, 0x87, 0x40, 0x4d, 0x58, 0x88, 0xbd, 0xe2, 0xba, 0x3e, 0x7e, 0x2d, 0xd7, 0x69,
    0xa9, 0x3c, 0x09, 0x64, 0x31, 0xb6, 0xcc, 0x4d, 0x1f, 0x23, 0xb6, 0x9e, 0x65, 0xd6, 0x81, 0xdc,
    0x85, 0xcc, 0x1e, 0xf1, 0x0b, 0x84, 0x38, 0xab, 0x93, 0x5f, 0x9f, 0x92, 0x4e, 0x93, 0x46, 0x95,
    0x6b, 0x3e, 0xb6, 0xc3, 0x1b, 0xd7, 0x69, 0xa1, 0x0a, 0x97, 0x37, 0x78, 0xed, 0xd1, 0x02, 0x81,
    0x80, 0x33, 0x18, 0xc3, 0x13, 0x65, 0x8e, 0x03, 0xc6, 0x9f, 0x90, 0x00, 0xae, 0x30, 0x19, 0x05,
    0x6f, 0x3c, 0x14, 0x6f, 0xea, 0xf8, 0x6b, 0x33, 0x5e, 0xee, 0xc7, 0xf6, 0x69, 0x2d, 0xdf, 0x44,
    0x76, 0xaa, 0x32, 0xba, 0x1a, 0x6e, 0xe6, 0x18, 0xa3, 0x17, 0x61, 0x1c, 0x92, 0x2d, 0x43, 0x5d,
    0x29, 0xa8, 0xdf, 0x14, 0xd8, 0xff, 0xdb, 0x38, 0xef, 0xb8, 0xb8, 0x2a, 0x96, 0x82, 0x8e, 0x68,
    0xf4, 0x19, 0x8c, 0x42, 0xbe, 0xcc, 0x4a, 0x31, 0x21, 0xd5, 0x35, 0x6c, 0x5b, 0xa5, 0x7c, 0xff,
    0xd1, 0x85, 0x87, 0x28, 0xdc, 0x97, 0x75, 0xe8, 0x03, 0x80, 0x1d, 0xfd, 0x25, 0x34, 0x41, 0x31,
    0x21, 0x12, 0x87, 0xe8, 0x9a, 0xb7, 0x6a, 0xc0, 0xc4, 0x89, 0x31, 0x15, 0x45, 0x0d, 0x9c, 0xee,
    0xf0, 0x6a, 0x2f, 0xe8, 0x59, 0x45, 0xc7, 0x7b, 0x0d, 0x6c, 0x55, 0xbb, 0x43, 0xca, 0xc7, 0x5a,
    0x01, 0x02, 0x81, 0x81, 0x00, 0xab, 0xf4, 0xd5, 0xcf, 0x78, 0x88, 0x82, 0xc2, 0xdd, 0xbc, 0x25,
    0xe6, 0xa2, 0xc1, 0xd2, 0x33, 0xdc, 0xef, 0x0a, 0x97, 0x2b, 0xdc, 0x59, 0x6a, 0x86, 0x61, 0x4e,
    0xa6, 0xc7, 0x95, 0x99, 0xa6, 0xa6, 0x55, 0x6c, 0x5a, 0x8e, 0x72, 0x25, 0x63, 0xac, 0x52, 0xb9,
    0x10, 0x69, 0x83, 0x99, 0xd3, 0x51, 0x6c, 0x1a, 0xb3, 0x83, 0x6a, 0xff, 0x50, 0x58, 0xb7, 0x28,
    0x97, 0x13, 0xe2, 0xba, 0x94, 0x5b, 0x89, 0xb4, 0xea, 0xba, 0x31, 0xcd, 0x78, 0xe4, 0x4a, 0x00,
    0x36, 0x42, 0x00, 0x62, 0x41, 0xc6, 0x47, 0x46, 0x37, 0xea, 0x6d, 0x50, 0xb4, 0x66, 0x8f, 0x55,
    0x0c, 0xc8, 0x99, 0x91, 0xd5, 0xec, 0xd2, 0x40, 0x1c, 0x24, 0x7d, 0x3a, 0xff, 0x74, 0xfa, 0x32,
    0x24, 0xe0, 0x11, 0x2b, 0x71, 0xad, 0x7e, 0x14, 0xa0, 0x77, 0x21, 0x68, 0x4f, 0xcc, 0xb6, 0x1b,
    0xe8, 0x00, 0x49, 0x13, 0x21, 0x02, 0x81, 0x81, 0x00, 0xb6, 0x18, 0x73, 0x59, 0x2c, 0x4f, 0x92,
    0xac, 0xa2, 0x2e, 0x5f, 0xb6, 0xbe, 0x78, 0x5d, 0x47, 0x71, 0x04, 0x92, 0xf0, 0xd7, 0xe8, 0xc5,
    0x7a, 0x84, 0x6b, 0xb8, 0xb4, 0x30, 0x1f, 0xd8, 0x0d, 0x58, 0xd0, 0x64, 0x80, 0xa7, 0x21, 0x1a,
    0x48, 0x00, 0x37, 0xd6, 0x19, 0x71, 0xbb, 0x91, 0x20, 0x9d, 0xe2, 0xc3, 0xec, 0xdb, 0x36, 0x1c,
    0xca, 0x48, 0x7d, 0x03, 0x32, 0x74, 0x1e, 0x65, 0x73, 0x02, 0x90, 0x73, 0xd8, 0x3f, 0xb5, 0x52,
    0x35, 0x79, 0x1c, 0xee, 0x93, 0xa3, 0x32, 0x8b, 0xed, 0x89, 0x98, 0xf1, 0x0c, 0xd8, 0x12, 0xf2,
    0x89, 0x7f, 0x32, 0x23, 0xec, 0x67, 0x66, 0x52, 0x83, 0x89, 0x99, 0x5e, 0x42, 0x2b, 0x42, 0x4b,
    0x84, 0x50, 0x1b, 0x3e, 0x47, 0x6d, 0x74, 0xfb, 0xd1, 0xa6, 0x10, 0x20, 0x6c, 0x6e, 0xbe, 0x44,
    0x3f, 0xb9, 0xfe, 0xbc, 0x8d, 0xda, 0xcb, 0xea, 0x8f
  ])


# Print packets with this function
def dump(*list):
    result = ""
    for element in list:
        result += (element if type(element) is str else repr(element)) + " "
    print(result)

# onRegisterFailed
def onRegisterFailed(prefix):
    print("Register failed for prefix " + prefix.toUri())

def promptAndInput(prompt):
    if sys.version_info[0] <= 2:
        return raw_input(prompt)
    else:
        return input(prompt)
        
def main():
    screenName = promptAndInput("Enter your name: ")

    defaultHubPrefix = "ndn/no/ntnu"
    hubPrefix = promptAndInput("Enter your hub prefix [" + defaultHubPrefix + "]: ")
    if hubPrefix == "":
        hubPrefix = defaultHubPrefix

    defaultpkList = "pklist"
    pkListName = promptAndInput("Sync with public key list [" + defaultpkList + "]: ")
    if pkListName == "":
        pkListName = defaultpkList

    host = "localhost" 
    # host = "129.241.208.115"
    print("Connecting to " + host + ", public Key List: " + pkListName + ", Name: " + screenName)
    print("")

    # Set up the key chain.
    face = Face(host)

    identityStorage = MemoryIdentityStorage()
    privateKeyStorage = MemoryPrivateKeyStorage()
    
    keyChain = KeyChain(IdentityManager(identityStorage, privateKeyStorage), NoVerifyPolicyManager())
    keyChain.setFace(face)
    
    keyName = Name("/testname/DSK-123")
    
    certificateName = keyName.getSubName(0, keyName.size() - 1).append("KEY").append(keyName[-1]).append("ID-CERT").append("0")
    identityStorage.addKey(keyName, KeyType.RSA, Blob(DEFAULT_RSA_PUBLIC_KEY_DER))
    privateKeyStorage.setKeyPairForKeyName(keyName, KeyType.RSA, DEFAULT_RSA_PUBLIC_KEY_DER, DEFAULT_RSA_PRIVATE_KEY_DER)
    face.setCommandSigningInfo(keyChain, certificateName)

    pkSync = PublicKeySync(screenName, pkListName, Name(hubPrefix), face, keyChain, certificateName)
    pkSync.initial()    

    # TODO:
    #    1. Generate new public key or use existing?
    #    2. Watch new public key
    #    3. sendUpdatedPublicKey if key is changed
    #    4. Download and store other keys
    #    5. Verify data packet
    while True:
        isReady, _, _ = select.select([sys.stdin], [], [], 0)
        if len(isReady) != 0:
            input = promptAndInput("")
            if input == "leave" or input == "exit":
                # We will send the leave message below.
                break

            pkSync.sendUpdatedPublicKey(input)

        pkSync.face.processEvents()
        # We need to sleep for a few milliseconds so we don't use 100% of the CPU.
        time.sleep(0.01)

    pkSync.unsubscribe()
    startTime = PublicKeySync.getNowMilliseconds()
    while True:
        if PublicKeySync.getNowMilliseconds() - startTime >= 1000.0:
            break

        face.processEvents()
        time.sleep(0.01)

    # Shutdown all services
    pkSync.face.shutdown()
    pkSync.observer.stop()
    pkSync.observer.join()

main()
