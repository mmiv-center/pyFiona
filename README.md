# pyFiona
By Njål
Upload local dicom data to Fiona using python.

Beware, Much Beta, bad documentation

Example
proj = FionaProject(name, regex, subj_template)
# name of project, e.g 'N-DOSE'
# regex with pattern <subj_id> and <event_id> to extract thos from dicom patient name tag
# e.g. r'NDOSE_(?P<subj_id>5\d{3})_(?P<event_id>\d)'
# sub_id template, what the subject id should look like r'N-DOSE_AD_{subj_id}'
# event id will be extracted from fiona, and converted by number in list

# generate coupling list. If no accession number, make new one using Hash of StudyUID
fiona_generate_coupling(dicom_dir, coupling_list)

# upload said cupling list
fiona_upload_coupling(coupling_list)

# scan folders and send to fiona, where the coupling list should be uploaded previously
send_dicom_folder(dicom_dir, fiona_addr, fiona_port, fiona_aet, 'NMPROC')