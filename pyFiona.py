#!/usr/bin/env python3
import os
import re
import requests
import pydicom as dicom
from tqdm import tqdm
from pynetdicom import AE, association
from hashlib import sha256

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

coupling_list = 'coupling_list.csv'
dicom_dir = '/mnt/storage/N-DOSE/dicom'
fiona_url  = 'https://fiona.ihelse.net/applications/Assign/php'
fiona_aet  = 'FIONA'
fiona_addr = '10.94.209.30'
fiona_port = 11112

# sopclass'es for Siemens mMR dicom files
spc = [
   { "SOPClassUID" : "1.2.840.10008.5.1.4.1.1.128", "TransferSyntaxUID": "1.2.840.10008.1.2.1" },
   { "SOPClassUID" : "1.2.840.10008.5.1.4.1.1.4", "TransferSyntaxUID": "1.2.840.10008.1.2.1" },
   { "SOPClassUID" : "1.2.840.10008.5.1.4.1.1.7", "TransferSyntaxUID": "1.2.840.10008.1.2.1" },
   { "SOPClassUID" : "1.2.840.10008.5.1.4.1.1.88.22", "TransferSyntaxUID": "1.2.840.10008.1.2.1" },
   { "SOPClassUID" : "1.3.12.2.1107.5.9.1", "TransferSyntaxUID": "1.2.840.10008.1.2.1" }
]

class FionaProject:
   def __init__(self, name: str, regex : str, subj_template: dict):
      self.name = name
      self._re = re.compile(regex)
      self._subj_temp = subj_template
      self.events = []
      self.subjects = []
      self.fiona_get_projinfo()
   
   def decode(self, text: str):
      result = self._re.match(text)
      if not result:
         return None
      data = result.groupdict()
      if int(data['event_id']) > len (self.events):
         print('Invalid event_id {data["event_id"]} for string {text}')
         return None
      subj_id = self._subj_temp.format(**data)
      event_id = self.events[int(data['event_id'])-1]
      return subj_id, event_id
   
   def fiona_create_subject(self, subj_id):
      if subj_id in self.subjects:
         return True
      print(f'Creating subject "{subj_id}" on fiona... ', end='')
      url = f'{fiona_url}/createNewName.php?action=set&project={self.name}&new_id={subj_id}'
      response = requests.get(url, verify=False)
      data = response.json()
      if 'error' in data:
         if data['error'] == 1:
            print(f'error ({data["message"]})')
            return False
      print(f'success({data["message"]})')
      self.subjects.append(subj_id)
      return True

   def fiona_get_projinfo(self):
      print(f'Fetching subject and event list for "{self.name}" project')
      url = f'{fiona_url}/infoForThisProject.php?project={self.name}'
      response = requests.get(url, verify=False)
      data = response.json()
      self.subjects = [p['record_id'] for p in data['participants']]
      self.events = list(data['events'].values())

def get_files_recursive(path: str):
   filelist = []
   for dirpath, dirs, files in os.walk(path):
      filelist.extend([os.path.join(dirpath, file) for file in files])
   return filelist

def scan_dicom_folder(dicom_path):
   studies = {}
   for dirpath, dirs, files in os.walk(dicom_path):
      for filename in files:
         filepath = os.path.join(dirpath, filename)
         try:
            with dicom.dcmread(filepath, defer_size='1 KB', stop_before_pixels=True) as dcm:
               patient_name = str(dcm.PatientName)
               generate_accession_number(dcm)
               accession = str(dcm.AccessionNumber)
            if patient_name not in studies:
               studies[patient_name] = accession
               print(f'Found {patient_name} {accession}')
            break # skip to next folder
         except Exception as e:
            print(e)
   print(f'Found {len(studies)} studies in {dicom_path}')
   return studies

def generate_accession_number(dcm):
   if len(dcm.AccessionNumber) > 0:
      return
   data = bytes(dcm.StudyInstanceUID, 'ascii')
   dcm.AccessionNumber = sha256(data).hexdigest()[-16:]

def fiona_upload_coupling(filename: str):
   print('Uploading coupling list... ',end='')
   url = f'{fiona_url}/upload-couplings-file.php'
   files = {'fileToUpload': open(filename,'rb')}
   response = requests.post(url, files=files, verify=False)
   print('done')
   print(response.text)

def fiona_generate_coupling(projects, dicom_path: str, filename: str):
   with open(filename,'w') as f:
      f.write('AccessionNumber,ProjectName,subjectid,eventname\r\n')
      for studyname, accession in scan_dicom_folder(dicom_path).items():
         for proj in projects:
            match = proj.decode(studyname)
            if match == None:
               continue
            subj_id, event_id = match
            if subj_id not in proj.subjects:
               proj.fiona_create_subject(subj_id)
            assign_string = f"{accession},{proj.name},{subj_id},{event_id}\r\n"
            f.write(assign_string)
            break

def send_dicom_folder(dicom_path: str, remote_host: str, remote_port: int, remote_aet: str, local_aet: str):
   print('Preparing to send dicom files')

   # setup application entry
   ae = AE(local_aet)
   for context in spc:
      ae.add_requested_context(context["SOPClassUID"], context["TransferSyntaxUID"])
      print(f'{context["SOPClassUID"], context["TransferSyntaxUID"]}')

   # Associated with peer
   assoc = ae.associate(remote_host, remote_port, ae_title=remote_aet)
   if not assoc.is_established:
      print(f"Could not connect to {remote_host}:{remote_port}")
   else:
      for folder in os.listdir(dicom_path):
         folder_path = os.path.join(dicom_path, folder)
         complete_path = os.path.join(folder_path,'complete')
         if os.path.isfile(complete_path):
            print(f'Skipping {folder_path}')
            continue
         filelist = get_files_recursive(folder_path)
         print(f'Sending {len(filelist)} files from folder {folder}')
         if send_dicom_filelist(assoc, filelist):
            with open(complete_path,'w') as f:
               f.write(f'{len(filelist)} files sent successfully')
   assoc.release()

def send_dicom_filelist(assoc: association, filelist: list):
   pbar = tqdm(total=len(filelist), unit=' files')
   for filepath in filelist:
      pbar.update()
      with dicom.dcmread(filepath) as dcm:
         generate_accession_number(dcm)
         status = assoc.send_c_store(dcm)
         # Check the status of the storage request
         if not status:
            # If the storage request succeeded this will be 0x0000
            pbar.close()
            print('Connection timed out, was aborted or received invalid response')
            print('C-STORE request status: 0x{0:04x}'.format(status.Status))
            return False
   pbar.close()
   return True      

def gen_projects():
   projects = [
      FionaProject(
         'N-DOSE_AD',
         r'NDOSE_(?P<subj_id>5\d{3})_(?P<event_id>\d)',
         r'N-DOSE_AD_{subj_id}'
      ),
      FionaProject(
         'N-DOSE',
         r'NDOSE_(?P<subj_id>3\d{3})_(?P<event_id>\d)',
         r'PD_{subj_id}'
      )
   ]
   return projects

if __name__ == '__main__':
   projects = gen_projects()
   fiona_generate_coupling(projects, dicom_dir, coupling_list)
   fiona_upload_coupling(coupling_list)
   send_dicom_folder(dicom_dir, fiona_addr, fiona_port, fiona_aet, 'NMPROC')